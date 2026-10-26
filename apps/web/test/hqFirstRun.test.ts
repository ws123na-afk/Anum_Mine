import assert from 'node:assert/strict';
import { test } from 'node:test';
import type { StarterCampusPreview } from '@anum/contracts';
import { buildingFitCamera, fitDistance, floorOverviewCamera, type Footprint } from '../src/lib/hqCamera.ts';
import { cameraBasis, dot, vsub, type CameraState } from '../src/lib/headquartersSpatial.ts';
import { parseCampusRequest } from '../src/lib/hqCampusCommand.ts';
import { canCreate, createCounts, INITIAL_SETUP } from '../src/hq-first-run/campusSetupModel.ts';

// The first-run campus flow and the floor overview (docs/ANUM_MINE_STARTER_CAMPUS.md#first-run, docs/ANUM_MINE_FLOOR_OVERVIEW.md).

/** Every corner of the box, in the picture of this camera: normalised screen coordinates (-1 to 1) with the pinhole maths of the renderer. */
function screenOf(camera: CameraState, box: Footprint, aspect: number): Array<{ x: number; y: number }> {
  const basis = cameraBasis(camera);
  const tv = Math.tan((camera.fov * Math.PI) / 360);
  const eye = { x: camera.target.x - basis.forward.x * camera.distance, y: camera.target.y - basis.forward.y * camera.distance, z: camera.target.z - basis.forward.z * camera.distance };
  const out: Array<{ x: number; y: number }> = [];
  for (const x of [box.x0, box.x1]) for (const z of [box.z0, box.z1]) for (const y of [box.y0, box.y1]) {
    const rel = vsub({ x, y, z }, eye);
    const depth = dot(rel, basis.forward);
    out.push({ x: dot(rel, basis.right) / depth / (tv * aspect), y: dot(rel, basis.up) / depth / tv });
  }
  return out;
}

// The footprint of ANUM HQ of the starter campus: 38 m by 24 m floors, five levels.
const HQ: Footprint = { x0: 0.8, x1: 39.2, z0: 0.8, z1: 25.2, y0: 12.4, y1: 16.2 };

test('the building camera keeps the whole footprint in the picture at every stage shape', () => {
  for (const aspect of [0.46, 0.75, 1, 1.5, 1.78, 2.4]) {
    const camera = buildingFitCamera(HQ, aspect);
    for (const p of screenOf(camera, HQ, aspect)) {
      assert.ok(Math.abs(p.x) <= 1 && Math.abs(p.y) <= 1, `aspect ${aspect}: corner at ${p.x.toFixed(2)}, ${p.y.toFixed(2)} is outside the picture`);
    }
  }
});

test('the old flat factor cropped the 38 m floor, the fitted camera does not', () => {
  const aspect = 1.5;
  const flat = Math.max(38.4 + 6, 24.4 + 6) * 0.8; // what frameBuilding returned: 0.8 of the longest side of the bounds
  const target = { x: 20, y: 14.3, z: 13 };
  const needed = fitDistance(target, HQ, { yaw: 35, pitch: 44, fov: 40 }, aspect, 1);
  assert.ok(needed > flat, `a distance of ${flat.toFixed(1)} m cannot fit; the fit needs ${needed.toFixed(1)} m`);
});

test('a narrower stage needs a farther camera, and the margin keeps the corners off the edge', () => {
  const wide = fitDistance({ x: 20, y: 14, z: 13 }, HQ, { yaw: 35, pitch: 44, fov: 40 }, 1.8, 1);
  const narrow = fitDistance({ x: 20, y: 14, z: 13 }, HQ, { yaw: 35, pitch: 44, fov: 40 }, 0.5, 1);
  assert.ok(narrow > wide);
  const camera = buildingFitCamera(HQ, 1.5);
  const worst = Math.max(...screenOf(camera, HQ, 1.5).map((p) => Math.max(Math.abs(p.x), Math.abs(p.y))));
  assert.ok(worst < 0.96, 'the corners keep a margin');
});

test('the floor overview looks from high above, north up, and shows the whole floor', () => {
  for (const aspect of [0.46, 1, 1.5, 2]) {
    const camera = floorOverviewCamera(HQ, aspect);
    assert.equal(camera.yaw, 0);
    assert.ok(camera.pitch >= 55 && camera.pitch <= 75);
    const floorOnly = { ...HQ, y1: HQ.y0 };
    for (const p of screenOf(camera, floorOnly, aspect)) assert.ok(Math.abs(p.x) <= 1 && Math.abs(p.y) <= 1, `aspect ${aspect}`);
  }
});

test('the cameras are valid for the renderer (distance inside its limits)', () => {
  for (const aspect of [0.3, 1.5, 3]) {
    for (const camera of [buildingFitCamera(HQ, aspect), floorOverviewCamera(HQ, aspect)]) {
      assert.ok(camera.distance >= camera.minDistance && camera.distance <= camera.maxDistance);
    }
  }
  // A tiny floor still gets a sane distance.
  assert.ok(buildingFitCamera({ x0: 0, x1: 4, z0: 0, z1: 4, y0: 0, y1: 3 }, 1.5).distance >= 14);
});

test('only a request for the campus itself shows the campus card', () => {
  for (const yes of ['set up my campus', 'Set up my campus.', 'create my campus', 'Please create the campus', 'setup the starter campus', 'build my campus now', 'can you set up my campus please', 'أنشئ حرمي', 'إعداد الحرم', 'جهّز الحرم الخاص بي', 'أنشئ لي الحرم من فضلك']) {
    assert.equal(parseCampusRequest(yes), true, yes);
  }
  for (const no of ['', 'add a building called North with 3 floors', 'create a task for the campus tour', 'show me the campus', 'what is on my campus', 'set up my campus and delete everything', 'أضف مبنى باسم الشمال', 'x'.repeat(300)]) {
    assert.equal(parseCampusRequest(no), false, no);
  }
});

const zero = { buildings: 0, floors: 0, rooms: 0, desks: 0 };
const preview = (over: Partial<StarterCampusPreview> = {}): StarterCampusPreview => ({
  version: 1, catalog_version: 1, workspace_id: 'ws', timezone: 'Asia/Dubai', selected: ['hq'], payload_hash: 'a'.repeat(64), can_confirm: true, buildings: [],
  totals: { create: { buildings: 8, floors: 18, rooms: 62, desks: 220 }, skip: zero, update: { room_departments: 0 } },
  rules: { creates_agents: false, seats_agents: false, changes_existing: false, fills_empty_departments: true }, ...over,
});

test('the create button needs a plan, a person who may confirm and something to create', () => {
  assert.equal(canCreate(INITIAL_SETUP), false, 'loading');
  assert.equal(canCreate({ ...INITIAL_SETUP, phase: 'ready', preview: preview() }), true);
  assert.equal(canCreate({ ...INITIAL_SETUP, phase: 'ready', preview: preview({ can_confirm: false }) }), false, 'not a workspace manager');
  assert.equal(canCreate({ ...INITIAL_SETUP, phase: 'ready', preview: preview({ totals: { create: zero, skip: zero, update: { room_departments: 0 } } }) }), false, 'already set up');
  for (const phase of ['creating', 'done', 'partial', 'failed', 'denied', 'error'] as const) assert.equal(canCreate({ ...INITIAL_SETUP, phase, preview: preview() }), false, phase);
  assert.deepEqual(createCounts(preview()), { buildings: 8, floors: 18, rooms: 62, desks: 220 });
  assert.equal(createCounts(null), null);
});
