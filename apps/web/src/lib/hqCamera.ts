/**
 * Camera presets and controls for the campus and building views, on top of the pure camera maths in
 * `headquartersSpatial.ts`. Everything returns a new CameraState; nothing touches the DOM.
 */
import { assertCamera, cameraBasis, dolly, dot, orbit, pan, vsub, type CameraState } from './headquartersSpatial.ts';
import type { Vec3 } from './headquartersProjection.ts';
import { PLAZA } from './campusModel.ts';

const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, v));

export const HOME_YAW = 35;
export const HOME_PITCH = 40;
export const TOP_PITCH = 87;

/** How much farther the camera must sit so a scene meant for a wide stage still fits a narrow one. */
export function aspectFit(aspect: number): number {
  if (!Number.isFinite(aspect) || aspect <= 0) return 1;
  return clamp(1.5 / aspect, 1, 2.6);
}

export function campusCamera(radius: number, centerZ = 0, aspect = 1.7): CameraState {
  const distance = clamp((radius * 2.05 + 38) * aspectFit(aspect), 64, 540);
  return { target: { x: 0, y: 0, z: centerZ }, distance, yaw: HOME_YAW, pitch: HOME_PITCH, fov: 40, minDistance: 14, maxDistance: 560 };
}

/**
 * What the home camera frames for a campus: the middle of its ground plan and the radius that holds it. The plaza sits at the origin and
 * the executive building stands far to the north, so the plan is lopsided; framing its box (the front gardens run on the +z side) instead of a circle
 * about the origin keeps the buildings as large as the stage allows.
 */
export function campusFraming(extent: Readonly<{ minX: number; maxX: number; minZ: number; maxZ: number; radius: number }>): { radius: number; centerZ: number } {
  // The plaza and its roundabout are always in the picture, whatever the buildings are.
  const plaza = PLAZA.outer + 2;
  const minZ = Math.min(extent.minZ - 4, -plaza), maxZ = Math.max(extent.maxZ + 40, plaza);
  const half = Math.max(Math.abs(extent.minX) + 6, Math.abs(extent.maxX) + 6, plaza);
  return { radius: Math.max(30, half, (maxZ - minZ) / 2), centerZ: (minZ + maxZ) / 2 };
}

export function buildingCamera(target: Vec3, distance: number, aspect = 1.7): CameraState {
  const d = clamp(distance * aspectFit(aspect), 14, 300);
  return { target, distance: d, yaw: HOME_YAW, pitch: 44, fov: 40, minDistance: 6, maxDistance: 320 };
}

export type Footprint = Readonly<{ x0: number; x1: number; z0: number; z1: number; y0: number; y1: number }>;

/**
 * The camera distance at which every corner of a box stays inside the picture for this angle and stage shape: a pinhole fit
 * (vertical field of view, `aspect` = width / height), so the turn of the isometric view, the height of the cutaway walls and a
 * narrow stage are all counted. A flat factor on the longest side cannot do that (it cropped the 38 m floors of the starter campus).
 */
export function fitDistance(target: Vec3, box: Footprint, angle: { yaw: number; pitch: number; fov: number }, aspect: number, margin = 1.12): number {
  const probe: CameraState = { target, distance: 10, yaw: angle.yaw, pitch: angle.pitch, fov: angle.fov, minDistance: 1, maxDistance: 1000 };
  const { right, up, forward } = cameraBasis(probe);
  const tv = Math.tan((angle.fov * Math.PI) / 360);
  const th = tv * (Number.isFinite(aspect) && aspect > 0.2 ? aspect : 1.5);
  let need = 0;
  for (const x of [box.x0, box.x1]) for (const z of [box.z0, box.z1]) for (const y of [box.y0, box.y1]) {
    const rel = vsub({ x, y, z }, target);
    const sx = Math.abs(dot(rel, right)), sy = Math.abs(dot(rel, up)), depth = dot(rel, forward);
    need = Math.max(need, sx / th - depth, sy / tv - depth);
  }
  return need * margin;
}

/** The home camera of a building: the whole footprint at the active floor, walls included, inside the stage at any size. */
export function buildingFitCamera(box: Footprint, aspect: number, pitch = 44, yaw = HOME_YAW): CameraState {
  const target = { x: (box.x0 + box.x1) / 2, y: (box.y0 + box.y1) / 2, z: (box.z0 + box.z1) / 2 };
  const distance = clamp(fitDistance(target, box, { yaw, pitch, fov: 40 }, aspect), 14, 300);
  return { target, distance, yaw, pitch, fov: 40, minDistance: 6, maxDistance: 320 };
}

/** Floor overview: the whole active floor seen from high above at a slight turn, so every room is on screen and readable. */
export function floorOverviewCamera(box: Footprint, aspect: number): CameraState {
  const target = { x: (box.x0 + box.x1) / 2, y: box.y0, z: (box.z0 + box.z1) / 2 };
  const angle = { yaw: 0, pitch: 62, fov: 40 };
  const distance = clamp(fitDistance(target, { ...box, y1: box.y0 + 0.4 }, angle, aspect, 1.1), 14, 300);
  return { target, distance, ...angle, minDistance: 6, maxDistance: 320 };
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

// ---------------------------------------------------------------- inside a room

/** A seated or standing person's eye height, in metres above the floor of the open floor. */
export const INSIDE_EYE_HEIGHT = 1.5;
const INSIDE_EYE = 0.05;

export type InsideRoom = Readonly<{ x: number; z: number; width: number; depth: number }>;

/**
 * "Inside" view of one room: a low interior camera at eye height, a metre in from the room's front wall and looking
 * across the room. The orbit pivot sits 5 cm from the eye, so turning (buttons, keys, drag) turns the view on the spot
 * instead of swinging the camera round the room. Pure: the caller decides whether to fly there or jump.
 */
export function insideCamera(room: InsideRoom, floorY: number, yaw = 0): CameraState {
  const margin = Math.min(0.9, room.depth * 0.25);
  const next: CameraState = {
    target: { x: room.x + room.width / 2, y: floorY + INSIDE_EYE_HEIGHT, z: room.z + room.depth - margin },
    distance: INSIDE_EYE, yaw, pitch: 7, fov: 62, minDistance: INSIDE_EYE, maxDistance: 0.4,
  };
  assertCamera(next);
  return next;
}

export function isInsideView(camera: CameraState): boolean {
  return camera.maxDistance <= 0.4 && camera.fov >= 60;
}

/** Keep the eye inside the room (a drag or the arrow keys cannot walk through a wall). */
export function clampInside(camera: CameraState, room: InsideRoom): CameraState {
  const m = 0.35;
  const x = Math.max(room.x + m, Math.min(room.x + room.width - m, camera.target.x));
  const z = Math.max(room.z + m, Math.min(room.z + room.depth - m, camera.target.z));
  return x === camera.target.x && z === camera.target.z ? camera : { ...camera, target: { ...camera.target, x, z } };
}
