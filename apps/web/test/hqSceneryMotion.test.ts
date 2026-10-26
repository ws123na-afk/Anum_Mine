import assert from 'node:assert/strict';
import { test } from 'node:test';
import { MeshBuilder, MAT, VERTEX_FLOATS } from '../src/lib/hqGeometry.ts';
import { DUSK, LIGHT_PRESETS, blendFor, lightingFor } from '../src/lib/hqLighting.ts';
import { targetBlend } from '../src/lib/hqLightPreset.ts';
import {
  SCENERY_FPS, LANE_SPEEDS, MAX_RIPPLES, MIN_GAP, sceneryLevelFor, readSceneryOverride, ripplesOf, sampleLane, trafficPlan, vehiclePose, type LanePose,
} from '../src/hqAssets/environment/scenery.ts';
import { SCENERY_VEHICLE_TRIANGLES_CAP, sceneryVehicleParts } from '../src/hqAssets/environment/sceneryVehicle.ts';
import { treeTemplate, tree } from '../src/hqAssets/environment/planting.ts';
import { buildExterior } from '../src/hqAssets/exterior/index.ts';
import { buildCampusScene } from '../src/lib/campusScene.ts';
import { STOREY } from '../src/hqAssets/exterior/parts.ts';
import { FLOOR_PITCH } from '../src/lib/buildingScene.ts';

const RING = { cx: 0, cz: 0, halfW: 110, halfD: 100, cornerRadius: 16 };

test('traffic: two lanes keep right, vehicles are spread so none overlaps, and the plan is deterministic', () => {
  const a = trafficPlan(RING, { seed: 's' }), b = trafficPlan(RING, { seed: 's' }), c = trafficPlan(RING, { seed: 'other' });
  assert.deepEqual(a.vehicles, b.vehicles);
  assert.notDeepEqual(a.vehicles.map((v) => v.start), c.vehicles.map((v) => v.start));
  assert.equal(a.lanes.length, 2);
  assert.ok(a.vehicles.length >= 6 && a.vehicles.length <= 18, String(a.vehicles.length));
  for (let li = 0; li < 2; li++) {
    const starts = a.vehicles.filter((v) => v.lane === li).map((v) => v.start).sort((x, y) => x - y);
    const total = a.lanes[li].total;
    for (let i = 0; i < starts.length; i++) {
      const gap = i + 1 < starts.length ? starts[i + 1] - starts[i] : total - starts[i] + starts[0];
      assert.ok(gap >= MIN_GAP - 0.01, `lane ${li} gap ${gap}`);
    }
    const speeds = new Set(a.vehicles.filter((v) => v.lane === li).map((v) => v.speed));
    assert.equal(speeds.size, 1, 'one speed per lane, so nobody catches up');
    assert.ok([...speeds][0] >= LANE_SPEEDS[li] && [...speeds][0] <= LANE_SPEEDS[li] + 0.5);
  }
  // Outer lane is counter-clockwise, inner clockwise, on screen with x east and z south.
  const area = (lane: typeof a.lanes[number]) => { let s = 0; const n = lane.pts.length / 2; for (let i = 0; i < n; i++) { const j = (i + 1) % n; s += lane.pts[i * 2] * lane.pts[j * 2 + 1] - lane.pts[j * 2] * lane.pts[i * 2 + 1]; } return s; };
  assert.ok(area(a.lanes[0]) * area(a.lanes[1]) < 0, 'the lanes run in opposite directions');
});

test('a vehicle stays on its lane, heads along it, wraps, and the same clock gives the same pose', () => {
  const plan = trafficPlan(RING, { seed: 's' });
  const pose: LanePose = { x: 0, z: 0, heading: 0 };
  for (let i = 0; i < plan.vehicles.length; i++) {
    const lane = plan.lanes[plan.vehicles[i].lane];
    for (const t of [0, 1.5, 37.2, 400]) {
      vehiclePose(plan, i, t, pose);
      assert.ok(Number.isFinite(pose.x) && Number.isFinite(pose.z) && Number.isFinite(pose.heading));
      // Distance to the nearest lane point is at most half the longest segment.
      let best = Infinity; const n = lane.pts.length / 2;
      for (let k = 0; k < n; k++) {
        const j = (k + 1) % n, x0 = lane.pts[k * 2], z0 = lane.pts[k * 2 + 1], dx = lane.pts[j * 2] - x0, dz = lane.pts[j * 2 + 1] - z0;
        const u = Math.max(0, Math.min(1, ((pose.x - x0) * dx + (pose.z - z0) * dz) / (dx * dx + dz * dz || 1)));
        best = Math.min(best, Math.hypot(x0 + dx * u - pose.x, z0 + dz * u - pose.z));
      }
      assert.ok(best < 0.05, `vehicle ${i} at ${t}: ${best}`);
    }
  }
  const a = { x: 0, z: 0, heading: 0 }, b = { x: 0, z: 0, heading: 0 };
  sampleLane(plan.lanes[0], 123.4, a); sampleLane(plan.lanes[0], 123.4 + plan.lanes[0].total, b);
  assert.ok(Math.abs(a.x - b.x) < 1e-3 && Math.abs(a.z - b.z) < 1e-3, 'one lap later it is back');
  // Moving forward: after one second the vehicle moved about its speed in the heading direction.
  vehiclePose(plan, 0, 10, a); vehiclePose(plan, 0, 11, b);
  const v = plan.vehicles[0].speed;
  assert.ok(Math.abs(Math.hypot(b.x - a.x, b.z - a.z) - v) < 1.2);
});

test('ripples keep the biggest water surfaces, at most four, and ignore bad input', () => {
  const r = ripplesOf([{ x: 0, z: 0, r: 9 }, { x: 5, z: 5, r: 3 }, { x: 1, z: 2, r: 7 }, { x: 9, z: 9, r: 5 }, { x: 8, z: 1, r: 4 }, { x: NaN, z: 0, r: 99 }, { x: 0, z: 0, r: 0 }]);
  assert.equal(r.length, MAX_RIPPLES * 3);
  assert.deepEqual(Array.from(r.slice(0, 3)), [0, 0, 9]);
  assert.equal(r[5], 7);
});

test('scenery level: still on a software renderer, under reduced motion and in a hidden tab; a person can pin a level; slow frames step it down', () => {
  const base = { software: false, reducedMotion: false, hidden: false, forced: null, pixels: 1_000_000, slowSteps: 0 } as const;
  assert.equal(sceneryLevelFor(base), 2);
  assert.equal(sceneryLevelFor({ ...base, pixels: 5_000_000 }), 1);
  assert.equal(sceneryLevelFor({ ...base, software: true }), 0);
  assert.equal(sceneryLevelFor({ ...base, software: true, forced: 1 }), 1);
  assert.equal(sceneryLevelFor({ ...base, reducedMotion: true }), 0);
  assert.equal(sceneryLevelFor({ ...base, reducedMotion: true, forced: 2 }), 0);
  assert.equal(sceneryLevelFor({ ...base, hidden: true, forced: 2 }), 0);
  assert.equal(sceneryLevelFor({ ...base, slowSteps: 1 }), 1);
  assert.equal(sceneryLevelFor({ ...base, slowSteps: 5 }), 0);
  assert.equal(SCENERY_FPS[0], 0);
  assert.ok(SCENERY_FPS[1] < SCENERY_FPS[2]);
  assert.equal(readSceneryOverride({ getItem: () => '1' }), 1);
  assert.equal(readSceneryOverride({ getItem: () => '7' }), null);
  assert.equal(readSceneryOverride({ getItem: () => 'on' }), 2);
  assert.equal(readSceneryOverride({ getItem: () => 'off' }), 0);
  assert.equal(readSceneryOverride({ getItem: () => { throw new Error('blocked'); } }), null);
  assert.equal(readSceneryOverride(null), null);
});

test('the instanced vehicle is three small parts with finite geometry, lights and no text', () => {
  const p = sceneryVehicleParts();
  assert.ok(p.triangles > 40 && p.triangles <= SCENERY_VEHICLE_TRIANGLES_CAP, String(p.triangles));
  for (const part of [p.body, p.trim, p.lights]) {
    assert.equal(part.length % (VERTEX_FLOATS * 3), 0);
    assert.ok(part.every(Number.isFinite));
  }
  let lit = 0;
  for (let i = 0; i < p.lights.length; i += VERTEX_FLOATS) if (p.lights[i + 9] > 0.5) lit += 1;
  assert.ok(lit > 0, 'head and tail lights are emissive');
});

test('light presets: day, blue hour and night differ, blue hour has lit windows and a warm horizon, and every value is finite', () => {
  assert.deepEqual([...LIGHT_PRESETS], ['day', 'dusk', 'night']);
  assert.equal(blendFor('day'), 0); assert.equal(blendFor('dusk'), DUSK); assert.equal(blendFor('night'), 1);
  const day = lightingFor(0), dusk = lightingFor(DUSK), night = lightingFor(1);
  assert.equal(day.lit, 0);
  assert.ok(dusk.lit > 0.9, 'windows are lit at the blue hour');
  assert.ok(night.lit > 0.99);
  assert.ok(dusk.glow[0] > dusk.glow[2] * 2, 'a warm band on the horizon');
  assert.ok(dusk.zenith[2] > dusk.zenith[0] * 2, 'a deep blue top');
  assert.ok(day.sunIntensity > dusk.sunIntensity && dusk.sunIntensity > night.sunIntensity);
  assert.ok(dusk.exposure >= day.exposure - 0.01);
  assert.ok(dusk.fog[2] > dusk.fog[1] - 0.001, 'bluish haze');
  for (let n = 0; n <= 1; n += 0.05) {
    const l = lightingFor(n);
    for (const v of [...l.sunDir, ...l.sunColor, ...l.sky, ...l.ground, ...l.fog, ...l.zenith, ...l.glow, l.sunIntensity, l.hemiIntensity, l.envIntensity, l.exposure, l.lit, l.cloud]) assert.ok(Number.isFinite(v));
  }
  assert.equal(targetBlend(false, 'dusk'), 0);
  assert.equal(targetBlend(true, 'dusk'), DUSK);
  assert.equal(targetBlend(true, 'night'), 1);
});

test('foliage and cloth carry their own surface class, so only leaves and flags move', () => {
  const tpl = treeTemplate('datePalm', 0);
  let foliage = 0, other = 0;
  for (let i = 0; i < tpl.vertices.length; i += VERTEX_FLOATS) { if (tpl.vertices[i + 10] === MAT.foliage) foliage += 1; else other += 1; }
  assert.ok(foliage > 50 && other > 20, `${foliage} leaf vertices, ${other} trunk vertices`);
  const b = new MeshBuilder();
  tree(b, 0, 0, { species: 'oak', seed: 'x', shadow: false });
  const v = b.toFloat32();
  const classes = new Set<number>();
  for (let i = 0; i < v.length; i += VERTEX_FLOATS) classes.add(v[i + 10]);
  assert.ok(classes.has(MAT.foliage) && classes.has(MAT.flat), 'the canopy is foliage, the trunk is not');
  const hq = buildExterior('executiveHq', { seed: 'f' }).mesh.vertices;
  const kinds = new Set<number>();
  for (let i = 0; i < hq.length; i += VERTEX_FLOATS) kinds.add(hq[i + 10]);
  assert.ok(kinds.has(MAT.cloth), 'flags are cloth');
  assert.ok(kinds.has(MAT.beacon), 'the aviation light is a beacon');
  // A flag's u coordinate runs from the pole (0) to the free end (1).
  let uMax = 0;
  for (let i = 0; i < hq.length; i += VERTEX_FLOATS) if (hq[i + 10] === MAT.cloth) { assert.ok(hq[i + 11] >= 0 && hq[i + 11] <= 1.0001); uMax = Math.max(uMax, hq[i + 11]); }
  assert.ok(uMax > 0.9);
});

test('the campus scene carries a traffic plan and ripples, all of it scenery with no names or counts', () => {
  const scene = buildCampusScene([
    { workspaceId: 'w1', x: 0, z: 0, width: 15, depth: 15, height: 16, levels: 3, kind: 'executive', icon: null },
    { workspaceId: 'w2', x: 45, z: 0, width: 17, depth: 14, height: 14, levels: 3, kind: 'business', icon: 'chart' },
    { workspaceId: 'w3', x: -45, z: 10, width: 11, depth: 10, height: 8, levels: 2, kind: 'personal', icon: null },
  ]);
  assert.ok(scene.motion.vehicles.length >= 6);
  assert.ok(scene.motion.ripples.length >= 3 && scene.motion.ripples.length <= MAX_RIPPLES * 3);
  assert.deepEqual(Object.keys(scene.motion).sort(), ['lanes', 'ripples', 'vehicles']);
  assert.ok(scene.buildings.every((bd) => bd.archetype !== 'villa' || bd.mesh.triangles > 500), 'the villa is an asset');
});

test('the exterior storey pitch is the interior floor pitch, so glass storeys line up with the stacked floors', () => {
  assert.equal(STOREY, FLOOR_PITCH);
});
