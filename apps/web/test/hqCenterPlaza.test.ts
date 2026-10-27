import assert from 'node:assert/strict';
import { test } from 'node:test';
import { MeshBuilder } from '../src/lib/hqGeometry.ts';
import { LOT, PLAZA, layoutCampus, lotBlocksCentre, lotOf, marginsOf, type CampusBuilding, type PlannedSize } from '../src/lib/campusModel.ts';
import { campusExtent } from '../src/lib/campusModel.ts';
import { campusCamera, campusFraming } from '../src/lib/hqCamera.ts';
import { layoutLabels } from '../src/lib/hqLabels.ts';
import { buildCampusScene } from '../src/lib/campusScene.ts';
import { SPOKE_START } from '../src/lib/campusSpokes.ts';
import { AVENUE_TRIANGLES_CAP, CENTER_PLAZA_TRIANGLES_CAP, ROUNDABOUT_TRIANGLES_CAP, avenue, centerPlaza, roundabout } from '../src/hqAssets/environment/plaza.ts';

// The centre of the campus (docs/ANUM_MINE_HQ_CENTER_PLAZA.md): where the buildings may stand, that the layout is stable,
// and that a walkway from the roundabout reaches every door. Everything here is scenery and layout: no name, count or status.
const bld = (id: string, kind: CampusBuilding['kind'] = 'business'): CampusBuilding => ({ workspace_id: id, tenant_id: 't', name: id, kind, icon: null, floors: 3, accessible: true, totals: null, presence_verified_at: null });
const KINDS: CampusBuilding['kind'][] = ['business', 'department', 'personal', 'business'];
/** `n` buildings: the first is the executive building, the others vary in kind. */
const campus = (n: number, exec = true): CampusBuilding[] => Array.from({ length: n }, (_, i) => (exec && i === 0 ? bld('hq', 'executive') : bld(`ws-${i * 7 + 3}x`, KINDS[i % KINDS.length])));

const overlap = (a: ReturnType<typeof lotOf>, b: ReturnType<typeof lotOf>) => a.x0 < b.x1 && a.x1 > b.x0 && a.z0 < b.z1 && a.z1 > b.z0;
const footprintGap = (p: { x: number; z: number; width: number; depth: number }, q: { x: number; z: number; width: number; depth: number }) =>
  Math.max(Math.abs(p.x - q.x) - (p.width + q.width) / 2, Math.abs(p.z - q.z) - (p.depth + q.depth) / 2);

test('from no building to twenty, every lot clears the plaza, both avenues and every other lot', () => {
  for (let n = 0; n <= 20; n++) {
    for (const exec of [true, false]) {
      const list = campus(n, exec);
      const placed = layoutCampus(list);
      assert.equal(placed.length, n);
      const lots = placed.map((p) => lotOf(p, marginsOf(p.kind, false)));
      lots.forEach((lot, i) => {
        assert.ok(!lotBlocksCentre(lot), `building ${i} of ${n} stands on the plaza or an avenue`);
        for (let j = i + 1; j < lots.length; j++) assert.ok(!overlap(lot, lots[j]), `lots ${i} and ${j} overlap with ${n} buildings`);
      });
      // The plaza itself: no footprint reaches the outer edge of the roundabout's pavement.
      for (const p of placed) {
        const dx = Math.max(p.x - p.width / 2, 0, -(p.x + p.width / 2)), dz = Math.max(p.z - p.depth / 2, 0, -(p.z + p.depth / 2));
        assert.ok(Math.hypot(dx, dz) > PLAZA.outer + 2, `${p.workspaceId} is too close to the roundabout`);
      }
    }
  }
});

test('with real floor plans the footprints are kept, the lots still never overlap and the plaza stays clear', () => {
  for (const n of [1, 3, 8, 13]) {
    const list = campus(n);
    const sizes = new Map<string, PlannedSize>(list.map((b, i) => [b.workspace_id, { w: 28 + (i % 3) * 8, d: 22 + (i % 2) * 8, levels: 4, height: 18 }]));
    const placed = layoutCampus(list, sizes);
    placed.forEach((p, i) => {
      assert.equal(p.width, sizes.get(p.workspaceId)!.w);
      assert.ok(!lotBlocksCentre(lotOf(p, marginsOf(p.kind, true))));
      for (let j = i + 1; j < placed.length; j++) assert.ok(footprintGap(p, placed[j]) > 2, `${p.workspaceId} and ${placed[j].workspaceId} are too close`);
    });
  }
});

test('the layout is deterministic, does not depend on the order of the list, and the executive building stands due north', () => {
  for (const n of [1, 2, 8, 16]) {
    const list = campus(n);
    const a = layoutCampus(list);
    assert.deepEqual(layoutCampus(list), a);
    const shuffled = list.slice().reverse();
    const byId = (rows: ReturnType<typeof layoutCampus>) => Object.fromEntries(rows.map((p) => [p.workspaceId, [p.x, p.z]]));
    assert.deepEqual(byId(layoutCampus(shuffled)), byId(a));
    assert.equal(a[0].kind, 'executive');
    assert.ok(Math.abs(a[0].x) < 1e-9 && a[0].z < -PLAZA.clear);
  }
});

test('adding or removing one building leaves most of the others where they were', () => {
  let total = 0, moved = 0;
  for (let n = 3; n <= 20; n++) {
    const small = layoutCampus(campus(n)), big = layoutCampus(campus(n + 1));
    const was = new Map(small.map((p) => [p.workspaceId, p]));
    let m = 0;
    for (const q of big) { const p = was.get(q.workspaceId); if (p && (Math.abs(p.x - q.x) > 0.01 || Math.abs(p.z - q.z) > 0.01)) m += 1; }
    assert.ok(m <= Math.max(4, Math.ceil(n * 0.6)), `adding one building to ${n} moved ${m}`);
    total += n; moved += m;
  }
  // The layout this replaced moved 54 percent of the buildings on average, and up to 13 at once.
  assert.ok(moved / total < 0.3, `on average ${(100 * moved / total).toFixed(0)} percent of the buildings moved`);
});

test('the scene draws the plaza, a roundabout and a walkway from the roundabout to every door, for any number of buildings', () => {
  for (const n of [0, 1, 3, 8, 13, 16, 20]) {
    const placed = layoutCampus(campus(n));
    const scene = buildCampusScene(placed);
    const { plaza } = scene;
    assert.deepEqual([plaza.center.x, plaza.center.z], [0, 0]);
    assert.equal(plaza.spokes.length, n);
    assert.ok(plaza.basin >= 10 && plaza.paved > plaza.basin && plaza.roadCentre > plaza.paved && plaza.outer > plaza.roadCentre);
    assert.deepEqual(Array.from(scene.motion.ripples.slice(0, 3)), [0, 0, PLAZA.basin], 'the fountain is the first ripple source');
    assert.equal(scene.motion.lanes.length, 3, 'traffic keeps to the ring road and the roundabout');
    for (const sp of plaza.spokes) {
      const b = scene.buildings.find((x) => x.workspaceId === sp.id)!;
      const first = sp.points[0], last = sp.points[sp.points.length - 1];
      assert.ok(Math.hypot(last[0] - b.door.x, last[1] - b.door.z) < 1e-6, `${sp.id}: the walkway ends at the door`);
      assert.ok(Math.abs(Math.hypot(first[0], first[1]) - SPOKE_START) < 0.05, `${sp.id}: the walkway starts on the roundabout`);
      // No stretch of the walkway crosses any building.
      for (let i = 0; i + 1 < sp.points.length; i++) {
        const [x0, z0] = sp.points[i], [x1, z1] = sp.points[i + 1];
        const steps = Math.max(1, Math.ceil(Math.hypot(x1 - x0, z1 - z0) / 0.5));
        for (let s = 0; s <= steps; s++) {
          const x = x0 + ((x1 - x0) * s) / steps, z = z0 + ((z1 - z0) * s) / steps;
          for (const p of placed) assert.ok(!(x > p.x - p.width / 2 && x < p.x + p.width / 2 && z > p.z - p.depth / 2 && z < p.z + p.depth / 2), `${sp.id} crosses ${p.workspaceId}`);
        }
      }
    }
    // The ring road, canal and gate still surround everything: every building lies inside the canal ring.
    const ring = scene.canalRadius - 24;
    for (const b of scene.buildings) {
      const bb = b.mesh.bounds;
      assert.ok(bb.min.x > -ring + 4 && bb.max.x < ring - 4 && bb.min.z > -ring + 4 && bb.max.z < ring - 4, `${b.workspaceId} reaches the ring road`);
    }
    // The vehicles on the roundabout are scenery: one speed per lane, a handful at most.
    const round = scene.motion.vehicles.filter((v) => v.lane === 2);
    assert.ok(round.length >= 1 && round.length <= 3);
    assert.equal(new Set(round.map((v) => v.speed)).size, 1);
  }
});

test('the scene is the same for the same buildings', () => {
  const a = buildCampusScene(layoutCampus(campus(8))), b = buildCampusScene(layoutCampus(campus(8)));
  assert.equal(a.triangles, b.triangles);
  assert.deepEqual(a.plaza.spokes, b.plaza.spokes);
  assert.deepEqual(Array.from(a.halos), Array.from(b.halos));
});

test('the plaza, roundabout and avenue builders stay inside their triangle budgets and are deterministic', () => {
  const run = (f: (b: MeshBuilder) => number) => { const b = new MeshBuilder(); const t = f(b); return { t, v: b.toFloat32() }; };
  const plaza = run((b) => centerPlaza(b, 0, 0).triangles);
  assert.ok(plaza.t > 2500 && plaza.t <= CENTER_PLAZA_TRIANGLES_CAP, String(plaza.t));
  assert.deepEqual(run((b) => centerPlaza(b, 0, 0).triangles).v, plaza.v);
  const ring = run((b) => roundabout(b, 0, 0, { mouths: [0, Math.PI / 2], crossings: [0.4, 1.2, 2.5, 4.4] }).triangles);
  assert.ok(ring.t > 500 && ring.t <= ROUNDABOUT_TRIANGLES_CAP, String(ring.t));
  const closed = run((b) => roundabout(b, 0, 0).triangles);
  assert.ok(ring.t < closed.t + 200, 'an opening leaves out kerbs');
  const av = run((b) => avenue(b, [0, 0], [90, 0]).triangles);
  assert.ok(av.t > 100 && av.t <= AVENUE_TRIANGLES_CAP, String(av.t));
  // The globe crowns the fountain: the plaza reaches above ten metres and stays inside its paved disc.
  const b = new MeshBuilder(); const r = centerPlaza(b, 0, 0);
  assert.ok(r.bounds.max.y > 9 && r.bounds.max.y < 16);
  assert.ok(Math.max(Math.abs(r.bounds.min.x), Math.abs(r.bounds.max.x), Math.abs(r.bounds.min.z), Math.abs(r.bounds.max.z)) <= PLAZA.paved + 2);
});

test('the lot margins leave room for the front garden on the door side', () => {
  assert.ok(LOT.front > LOT.back);
  const l = lotOf({ x: 0, z: 0, width: 10, depth: 10 });
  assert.deepEqual([l.x0, l.x1, l.z0, l.z1], [-5 - LOT.side, 5 + LOT.side, -5 - LOT.back, 5 + LOT.front]);
});

test('the home camera frames every building and the plaza, centred on the lopsided plan and not on the origin', () => {
  for (const n of [0, 1, 4, 8, 16, 20]) {
    const placed = layoutCampus(campus(n));
    const f = campusFraming(campusExtent(placed));
    assert.ok(f.radius >= 30);
    for (const p of placed) {
      assert.ok(Math.abs(p.x) + p.width / 2 <= f.radius, `${p.workspaceId} is outside the framing sideways`);
      assert.ok(p.z - p.depth / 2 >= f.centerZ - f.radius && p.z + p.depth / 2 <= f.centerZ + f.radius, `${p.workspaceId} is outside the framing north or south`);
    }
    assert.ok(Math.abs(f.centerZ) + PLAZA.outer <= f.radius, 'the plaza at the origin is inside the framing');
    const cam = campusCamera(f.radius, f.centerZ, 1.6);
    assert.equal(cam.target.z, f.centerZ);
  }
  // With the executive building due north the framing centre moves north of the plaza.
  assert.ok(campusFraming(campusExtent(layoutCampus(campus(4)))).centerZ < 0);
});

test('building cards are never hidden on a crowded stage: they give up detail and one is never lost', () => {
  const vp = new Float32Array([1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]);
  const labels = Array.from({ length: 8 }, (_, i) => ({ id: `b${i}`, anchor: { x: ((i % 4) - 1.5) * 0.12, y: 0.1 * ((i % 2) - 0.5), z: 0.5 }, priority: 1, tiers: ['full', 'compact', 'dot'] as const, keep: true }));
  const sizeOf = (_: string, d: string) => (d === 'full' ? { w: 250, h: 58 } : d === 'compact' ? { w: 140, h: 46 } : { w: 44, h: 40 });
  const placed = layoutLabels(labels, vp, 600, 360, sizeOf, 64, { keepOut: [{ x0: 0, y0: 200, x1: 240, y1: 360 }] });
  assert.ok(placed.every((p) => p.shown), 'every building card is shown');
  assert.ok(placed.some((p) => p.density !== 'full'), 'some gave up detail');
});
