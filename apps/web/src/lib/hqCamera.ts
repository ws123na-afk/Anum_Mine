/**
 * Camera presets and controls for the campus and building views, on top of the pure camera maths in
 * `headquartersSpatial.ts`. Everything returns a new CameraState; nothing touches the DOM.
 */
import { assertCamera, dolly, orbit, pan, type CameraState } from './headquartersSpatial.ts';
import type { Vec3 } from './headquartersProjection.ts';

const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, v));

export const HOME_YAW = 35;
export const HOME_PITCH = 44;
export const TOP_PITCH = 87;

/** How much farther the camera must sit so a scene meant for a wide stage still fits a narrow one. */
export function aspectFit(aspect: number): number {
  if (!Number.isFinite(aspect) || aspect <= 0) return 1;
  return clamp(1.5 / aspect, 1, 2.6);
}

export function campusCamera(radius: number, centerZ = 0, aspect = 1.7): CameraState {
  const distance = clamp((radius * 2.7 + 50) * aspectFit(aspect), 70, 540);
  return { target: { x: 0, y: 0, z: centerZ }, distance, yaw: HOME_YAW, pitch: HOME_PITCH, fov: 40, minDistance: 14, maxDistance: 560 };
}

export function buildingCamera(target: Vec3, distance: number, aspect = 1.7): CameraState {
  const d = clamp(distance * aspectFit(aspect), 14, 300);
  return { target, distance: d, yaw: HOME_YAW, pitch: 50, fov: 40, minDistance: 6, maxDistance: 320 };
}

/** The same framing from straight above (the "Top" switch), north up like the minimap. */
export function topView(camera: CameraState): CameraState {
  const next = { ...camera, pitch: TOP_PITCH, yaw: 0 };
  assertCamera(next);
  return next;
}

export function isTopView(camera: CameraState): boolean {
  return camera.pitch >= TOP_PITCH - 3;
}

/** Back to the isometric 3D angle at the current framing. */
export function threeDView(camera: CameraState): CameraState {
  const next = { ...camera, pitch: HOME_PITCH, yaw: camera.pitch >= TOP_PITCH - 3 ? HOME_YAW : camera.yaw };
  assertCamera(next);
  return next;
}

/** North up: yaw 0 puts the viewer on the south side looking north (the minimap and compass agree). */
export function faceNorth(camera: CameraState): CameraState {
  return { ...camera, yaw: 0 };
}

export function rotateBy(camera: CameraState, degrees: number): CameraState {
  return { ...camera, yaw: (((camera.yaw + degrees) % 360) + 360) % 360 };
}

export function zoomBy(camera: CameraState, factor: number): CameraState {
  // dolly() takes a wheel-like delta: ln(factor) / .001 gives the same exponential step.
  return dolly(camera, Math.log(factor) / 0.001);
}

/** Keep the focus point over the ground that exists so panning cannot lose the scene. */
export function clampTarget(camera: CameraState, radius: number): CameraState {
  const limit = Math.max(10, radius);
  const t = camera.target;
  const len = Math.hypot(t.x, t.z);
  if (len <= limit) return camera;
  const k = limit / len;
  return { ...camera, target: { x: t.x * k, y: t.y, z: t.z * k } };
}

/** The shortest signed turn from one yaw to another, in degrees. */
export function yawDelta(from: number, to: number): number {
  return ((((to - from) % 360) + 540) % 360) - 180;
}

/** Retarget a camera so a tween from `from` takes the short way round. */
export function alignYaw(from: CameraState, to: CameraState): CameraState {
  return { ...to, yaw: from.yaw + yawDelta(from.yaw, to.yaw) };
}

export type CameraKeyAction =
  | { kind: 'pan'; dx: number; dy: number }
  | { kind: 'rotate'; degrees: number }
  | { kind: 'zoom'; factor: number }
  | { kind: 'reset' };

/** Keyboard on the focused scene: arrows pan, Q and E rotate, plus and minus zoom, 0 resets. */
export function keyToCameraAction(key: string, shift = false): CameraKeyAction | null {
  const step = shift ? 160 : 70;
  switch (key) {
    case 'ArrowLeft': return { kind: 'pan', dx: step, dy: 0 };
    case 'ArrowRight': return { kind: 'pan', dx: -step, dy: 0 };
    case 'ArrowUp': return { kind: 'pan', dx: 0, dy: step };
    case 'ArrowDown': return { kind: 'pan', dx: 0, dy: -step };
    case 'q': case 'Q': return { kind: 'rotate', degrees: -15 };
    case 'e': case 'E': return { kind: 'rotate', degrees: 15 };
    case '+': case '=': return { kind: 'zoom', factor: 0.8 };
    case '-': case '_': return { kind: 'zoom', factor: 1.25 };
    case '0': return { kind: 'reset' };
    default: return null;
  }
}

export function applyCameraAction(camera: CameraState, action: CameraKeyAction, home: CameraState): CameraState {
  switch (action.kind) {
    case 'pan': return pan(camera, action.dx, action.dy);
    case 'rotate': return rotateBy(camera, action.degrees);
    case 'zoom': return zoomBy(camera, action.factor);
    case 'reset': return home;
  }
}

export { orbit };

// ---------------------------------------------------------------- minimap

import { cameraEye, hitPlane, screenRay, vadd, vscale } from './headquartersSpatial.ts';

/**
 * The ground area the camera currently sees, as four world points (x, z) in screen-corner order
 * top-left, top-right, bottom-right, bottom-left. A ray that misses the ground is cut at three
 * times the camera distance so the outline stays finite in a low, flat view.
 */
export function viewFootprint(camera: CameraState, aspect: number): Array<{ x: number; z: number }> {
  const corners: Array<[number, number]> = [[-1, 1], [1, 1], [1, -1], [-1, -1]];
  const eye = cameraEye(camera);
  return corners.map(([nx, ny]) => {
    const ray = screenRay(camera, nx, ny, aspect);
    const hit = hitPlane(ray, 0);
    const far = camera.distance * 3;
    const p = hit && Math.hypot(hit.x - eye.x, hit.z - eye.z) < far ? hit : vadd(ray.origin, vscale({ x: ray.direction.x, y: 0, z: ray.direction.z }, far));
    return { x: p.x, z: p.z };
  });
}

/** World (x, z) to minimap pixels for a square map covering [-extent, extent], north up. */
export function worldToMap(x: number, z: number, extent: number, size: number): { x: number; y: number } {
  const e = Math.max(1, extent);
  return { x: ((x + e) / (2 * e)) * size, y: ((z + e) / (2 * e)) * size };
}

/** Minimap pixels back to world (x, z), used to jump the camera when the map is clicked. */
export function mapToWorld(px: number, py: number, extent: number, size: number): { x: number; z: number } {
  const e = Math.max(1, extent);
  return { x: (px / size) * 2 * e - e, z: (py / size) * 2 * e - e };
}
