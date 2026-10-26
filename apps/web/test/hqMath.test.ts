import assert from 'node:assert/strict';
import { test } from 'node:test';
import { aabbInFrustum, frustumPlanes, mat4Multiply, mat4Perspective, mat4LookAt, ndcToPixels, pickNearest, projectToNdc, rayAabb, viewProjection } from '../src/lib/hqMath.ts';
import { screenRay, initialCamera, cameraEye } from '../src/lib/headquartersSpatial.ts';
import { campusCamera } from '../src/lib/hqCamera.ts';

const camera = campusCamera(60);

test('the camera target projects to the middle of the screen', () => {
  const vp = viewProjection(camera, 16 / 9);
  const p = projectToNdc(vp, camera.target);
  assert.ok(p.visible);
  assert.ok(Math.abs(p.x) < 1e-3 && Math.abs(p.y) < 1e-3);
});

test('points behind the camera are not visible', () => {
  const vp = viewProjection(camera, 1.5);
  const eye = cameraEye(camera);
  const behind = { x: eye.x * 2, y: eye.y * 2, z: eye.z * 2 };
  assert.equal(projectToNdc(vp, behind).visible, false);
});

test('a screen ray through a projected point hits that point (projection and picking agree)', () => {
  const aspect = 1.6;
  const vp = viewProjection(camera, aspect);
  const world = { x: 12, y: 0, z: -9 };
  const ndc = projectToNdc(vp, world);
  const ray = screenRay(camera, ndc.x, ndc.y, aspect);
  const t = -ray.origin.y / ray.direction.y;
  assert.ok(Math.abs(ray.origin.x + ray.direction.x * t - world.x) < 0.02);
  assert.ok(Math.abs(ray.origin.z + ray.direction.z * t - world.z) < 0.02);
});

test('ndc converts to pixels with y flipped', () => {
  assert.deepEqual(ndcToPixels({ x: -1, y: 1 }, 200, 100), { x: 0, y: 0 });
  assert.deepEqual(ndcToPixels({ x: 1, y: -1 }, 200, 100), { x: 200, y: 100 });
  assert.deepEqual(ndcToPixels({ x: 0, y: 0 }, 200, 100), { x: 100, y: 50 });
});

test('frustum culling keeps visible boxes and drops far-off ones', () => {
  const planes = frustumPlanes(viewProjection(camera, 1.6));
  const near = { min: { x: -5, y: 0, z: -5 }, max: { x: 5, y: 8, z: 5 } };
  const away = { min: { x: 9000, y: 0, z: 9000 }, max: { x: 9010, y: 8, z: 9010 } };
  assert.equal(aabbInFrustum(planes, near), true);
  assert.equal(aabbInFrustum(planes, away), false);
});

test('ray and box intersection returns the entry distance, null on a miss', () => {
  const box = { min: { x: -1, y: -1, z: -1 }, max: { x: 1, y: 1, z: 1 } };
  assert.equal(rayAabb({ origin: { x: 0, y: 0, z: 10 }, direction: { x: 0, y: 0, z: -1 } }, box), 9);
  assert.equal(rayAabb({ origin: { x: 5, y: 0, z: 10 }, direction: { x: 0, y: 0, z: -1 } }, box), null);
  assert.equal(rayAabb({ origin: { x: 0, y: 0, z: 10 }, direction: { x: 0, y: 0, z: 1 } }, box), null);
});

test('pickNearest chooses the closer of two overlapping boxes', () => {
  const a = { id: 'near', box: { min: { x: -1, y: 0, z: 0 }, max: { x: 1, y: 2, z: 2 } } };
  const b = { id: 'far', box: { min: { x: -1, y: 0, z: -6 }, max: { x: 1, y: 2, z: -4 } } };
  const hit = pickNearest({ origin: { x: 0, y: 1, z: 10 }, direction: { x: 0, y: 0, z: -1 } }, [b, a]);
  assert.equal(hit?.id, 'near');
});

test('matrix multiply with identity-like lookAt keeps the origin in front of the camera', () => {
  const view = mat4LookAt({ x: 0, y: 0, z: 5 }, { x: 0, y: 0, z: 0 });
  const proj = mat4Perspective(60, 1, 0.1, 100);
  const vp = mat4Multiply(proj, view);
  assert.ok(projectToNdc(vp, { x: 0, y: 0, z: 0 }).visible);
  assert.throws(() => mat4Perspective(60, 0, 0.1, 100));
  assert.throws(() => mat4LookAt({ x: 0, y: 1, z: 0 }, { x: 0, y: 1, z: 0 }));
});

test('initial building camera framing still works with the new matrices', () => {
  const cam = initialCamera({ id: 'f', name: 'F', level: 0, rooms: [], desks: [], bounds: { width: 20, depth: 16 } });
  assert.ok(projectToNdc(viewProjection(cam, 1.5), cam.target).visible);
});
