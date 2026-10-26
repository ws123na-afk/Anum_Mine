import assert from 'node:assert/strict';
import { test } from 'node:test';
import { DUSK } from '../src/lib/hqLighting.ts';
import { HALO_FLOATS, fitShadow, focusRange, gradeFor, kelvin, pickLights, pointLightGain, sunOnScreen } from '../src/lib/hqLightRig.ts';
import { viewProjection } from '../src/lib/hqMath.ts';
import { cameraEye, type CameraState } from '../src/lib/headquartersSpatial.ts';

const cam = (over: Partial<CameraState> = {}): CameraState => ({ target: { x: 10, y: 0, z: -5 }, distance: 60, yaw: 0.6, pitch: 0.7, fov: 40, minDistance: 8, maxDistance: 400, ...over }) as CameraState;
const region = { center: { x: 0, y: 0, z: 0 }, radius: 150 };

test('the fitted shadow follows the camera distance, stays inside the region and snaps to whole texels', () => {
  const near = fitShadow(cam({ distance: 30 }), region, 4096), far = fitShadow(cam({ distance: 300 }), region, 4096);
  assert.ok(near.radius < far.radius, `${near.radius} ${far.radius}`);
  assert.ok(far.radius <= region.radius);
  assert.ok(near.radius >= 24);
  const edge = fitShadow(cam({ target: { x: 400, y: 0, z: 0 } }), region, 4096);
  assert.ok(Math.hypot(edge.center.x, edge.center.z) <= region.radius, 'centre pulled back inside');
  // Moving the camera by less than a texel step leaves the centre where it was; a large move changes it.
  const a = fitShadow(cam(), region, 4096), b = fitShadow(cam({ target: { x: 10.2, y: 0, z: -5 } }), region, 4096), c = fitShadow(cam({ target: { x: 30, y: 0, z: -5 } }), region, 4096);
  assert.deepEqual(a.center, b.center);
  assert.notDeepEqual(a.center, c.center);
  const texel = (2 * a.radius) / 4096;
  assert.ok(Math.abs(a.center.x / (texel * 16) - Math.round(a.center.x / (texel * 16))) < 1e-6);
});

test('the nearest warm lights are picked, deterministically, within range', () => {
  const halos = new Float32Array([
    5, 3, 5, 6, 1, 0.8, 0.5, 1,
    50, 3, 0, 6, 1, 0.8, 0.5, 1,
    200, 3, 0, 6, 1, 0.8, 0.5, 1,
    -2, 3, 1, 4, 0.4, 0.6, 1, 0.5,
  ]);
  assert.equal(halos.length, HALO_FLOATS * 4);
  const lights = pickLights(halos, { x: 0, y: 0, z: 0 }, 2);
  assert.deepEqual(lights.map((l) => l.x), [-2, 5]);
  assert.equal(pickLights(halos, { x: 0, y: 0, z: 0 }, 9).length, 3, 'the one 200 m away is out of range');
  assert.deepEqual(pickLights(undefined, { x: 0, y: 0, z: 0 }, 4), []);
  assert.deepEqual(pickLights(halos, { x: 0, y: 0, z: 0 }, 0), []);
  assert.deepEqual(pickLights(halos, { x: 0, y: 0, z: 0 }, 2), lights);
  assert.ok(lights[0].distance >= 4);
});

test('the interior lights are off by day and fully on from the blue hour', () => {
  assert.equal(pointLightGain(0), 0);
  assert.ok(pointLightGain(0.3) > 0 && pointLightGain(0.3) < 1);
  assert.equal(pointLightGain(DUSK), 1);
  assert.equal(pointLightGain(1), 1);
});

test('colour temperature runs from warm orange to cool white', () => {
  const warm = kelvin(2700), cool = kelvin(6500), blue = kelvin(9000);
  assert.ok(warm[0] > warm[1] && warm[1] > warm[2], `${warm}`);
  assert.ok(cool.every((c) => c > 0.9), `${cool}`);
  assert.ok(blue[2] >= blue[0], `${blue}`);
  for (const k of [500, 2700, 6500, 20000]) for (const c of kelvin(k)) assert.ok(c >= 0 && c <= 1);
});

test('the sun is found on screen when it is in front, and the shafts vanish when it is behind the camera', () => {
  const c = cam({ yaw: 0.3, pitch: 0.5 });
  const vp = viewProjection(c, 16 / 9);
  const eye = cameraEye(c);
  const f = [c.target.x - eye.x, c.target.y - eye.y, c.target.z - eye.z];
  const len = Math.hypot(f[0], f[1], f[2]);
  const forward: [number, number, number] = [f[0] / len, f[1] / len, f[2] / len];
  // Light travels away from the sun: with the sun straight ahead, the light travels towards the camera.
  const ahead = sunOnScreen(vp, c, [-forward[0], -forward[1], -forward[2]]);
  assert.ok(Math.abs(ahead.x) < 0.01 && Math.abs(ahead.y) < 0.01, `${ahead.x} ${ahead.y}`);
  assert.equal(ahead.weight, 1);
  assert.equal(sunOnScreen(vp, c, forward).weight, 0, 'behind the camera');
  const side = sunOnScreen(vp, c, [-forward[0] - forward[2] * 0.9, -forward[1], -forward[2] + forward[0] * 0.9]);
  assert.ok(side.weight < 1, 'off to the side it fades');
});

test('each preset has its own grade: the blue hour has the strongest shafts, night the most grain and vignette', () => {
  const day = gradeFor(0), hour = gradeFor(DUSK), night = gradeFor(1);
  assert.ok(hour.shafts > day.shafts && hour.shafts > night.shafts);
  assert.ok(night.grain > hour.grain && hour.grain > day.grain);
  assert.ok(night.vignette > day.vignette);
  for (const g of [day, hour, night]) for (const v of Object.values(g)) assert.ok(Number.isFinite(v) && v >= 0);
  const mid = gradeFor(0.3);
  assert.ok(mid.grain > day.grain && mid.grain < hour.grain);
  assert.deepEqual(gradeFor(-1), gradeFor(0));
  assert.deepEqual(gradeFor(5), gradeFor(1));
});

test('depth of field keeps the focus distance, softens less when the camera is far away, and caps the blur', () => {
  const near = focusRange(30), far = focusRange(400);
  assert.equal(near.focus, 30);
  assert.ok(near.aperture > far.aperture);
  assert.ok(near.maxBlur <= 0.01);
  assert.equal(focusRange(1).focus, 8);
});
