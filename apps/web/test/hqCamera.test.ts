import assert from 'node:assert/strict';
import { test } from 'node:test';
import { alignYaw, applyCameraAction, campusCamera, clampTarget, faceNorth, isTopView, keyToCameraAction, mapToWorld, rotateBy, threeDView, topView, viewFootprint, worldToMap, yawDelta, zoomBy } from '../src/lib/hqCamera.ts';

const home = campusCamera(60);

test('rotate wraps and top/3D switches keep the framing', () => {
  assert.equal(rotateBy({ ...home, yaw: 350 }, 20).yaw, 10);
  assert.equal(rotateBy({ ...home, yaw: 5 }, -20).yaw, 345);
  const top = topView(home);
  assert.ok(isTopView(top));
  assert.equal(top.distance, home.distance);
  assert.ok(!isTopView(threeDView(top)));
  assert.equal(faceNorth(home).yaw, 0);
});

test('zoom stays inside the allowed range', () => {
  let c = home;
  for (let i = 0; i < 60; i++) c = zoomBy(c, 0.5);
  assert.equal(c.distance, c.minDistance);
  for (let i = 0; i < 60; i++) c = zoomBy(c, 2);
  assert.equal(c.distance, c.maxDistance);
  assert.ok(zoomBy(home, 0.8).distance < home.distance);
});

test('yaw tween takes the short way round', () => {
  assert.equal(yawDelta(350, 10), 20);
  assert.equal(yawDelta(10, 350), -20);
  assert.equal(alignYaw({ ...home, yaw: 350 }, { ...home, yaw: 10 }).yaw, 370);
});

test('panning is clamped to the ground', () => {
  const far = clampTarget({ ...home, target: { x: 900, y: 0, z: 0 } }, 100);
  assert.equal(Math.round(far.target.x), 100);
  assert.equal(clampTarget(home, 100), home);
});

test('keyboard maps to camera actions', () => {
  assert.deepEqual(keyToCameraAction('q'), { kind: 'rotate', degrees: -15 });
  assert.deepEqual(keyToCameraAction('+'), { kind: 'zoom', factor: 0.8 });
  assert.equal(keyToCameraAction('x'), null);
  assert.equal(applyCameraAction(home, { kind: 'reset' }, home), home);
});

test('view footprint is four finite ground points that contain the target', () => {
  const pts = viewFootprint(home, 1.7);
  assert.equal(pts.length, 4);
  for (const p of pts) assert.ok(Number.isFinite(p.x) && Number.isFinite(p.z));
  const xs = pts.map((p) => p.x), zs = pts.map((p) => p.z);
  assert.ok(Math.min(...xs) < 0 && Math.max(...xs) > 0 && Math.min(...zs) < 0 && Math.max(...zs) > 0);
});

test('minimap coordinates round trip', () => {
  const m = worldToMap(30, -20, 100, 200);
  const w = mapToWorld(m.x, m.y, 100, 200);
  assert.ok(Math.abs(w.x - 30) < 1e-9 && Math.abs(w.z + 20) < 1e-9);
  assert.deepEqual(worldToMap(0, 0, 100, 200), { x: 100, y: 100 });
});

import { INSIDE_EYE_HEIGHT, clampInside, insideCamera, isInsideView } from '../src/lib/hqCamera.ts';
import { cameraEye } from '../src/lib/headquartersSpatial.ts';
import { test as insideTest } from 'node:test';
import insideAssert from 'node:assert/strict';

insideTest('the inside camera sits at eye height inside the room and turns on the spot', () => {
  const room = { x: 10, z: 4, width: 8, depth: 5 };
  const cam = insideCamera(room, 4.26);
  const eye = cameraEye(cam);
  insideAssert.ok(Math.abs(eye.y - (4.26 + INSIDE_EYE_HEIGHT)) < 0.1);
  insideAssert.ok(eye.x > room.x && eye.x < room.x + room.width && eye.z > room.z && eye.z < room.z + room.depth);
  insideAssert.ok(isInsideView(cam));
  const turned = cameraEye({ ...cam, yaw: cam.yaw + 90 });
  insideAssert.ok(Math.hypot(turned.x - eye.x, turned.z - eye.z) < 0.1, 'rotating keeps the eye where it is');
});

insideTest('the inside camera cannot be walked through a wall', () => {
  const room = { x: 0, z: 0, width: 6, depth: 4 };
  const cam = insideCamera(room, 0);
  const out = clampInside({ ...cam, target: { ...cam.target, x: 50, z: -20 } }, room);
  insideAssert.ok(out.target.x <= 6 - 0.35 && out.target.z >= 0.35);
});
