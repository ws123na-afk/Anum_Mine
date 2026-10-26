/**
 * Matrix, projection, frustum and picking maths for the ANUM MINE headquarters scenes.
 *
 * Pure functions on plain numbers: no DOM, no WebGL, so the unit runner covers them. The camera
 * convention is the one in `headquartersSpatial.ts` (target, distance, yaw, pitch, vertical fov in
 * degrees, +y up); the matrices here are column-major, the layout WebGL expects.
 */
import { cameraEye, type CameraState, type Ray } from './headquartersSpatial.ts';
import type { Vec3 } from './headquartersProjection.ts';

export type Mat4 = Float32Array;
export type Plane = readonly [number, number, number, number];
export type Aabb = Readonly<{ min: Vec3; max: Vec3 }>;
export type ScreenPoint = Readonly<{ x: number; y: number; depth: number; visible: boolean }>;

const rad = (degrees: number) => (degrees * Math.PI) / 180;

export function mat4Identity(): Mat4 {
  const out = new Float32Array(16);
  out[0] = out[5] = out[10] = out[15] = 1;
  return out;
}

export function mat4Perspective(fovDegrees: number, aspect: number, near: number, far: number): Mat4 {
  if (!(aspect > 0) || !Number.isFinite(aspect)) throw new RangeError('Aspect ratio must be positive');
  if (!(near > 0) || far <= near) throw new RangeError('Clip planes must satisfy 0 < near < far');
  const f = 1 / Math.tan(rad(fovDegrees) / 2);
  const out = new Float32Array(16);
  out[0] = f / aspect;
  out[5] = f;
  out[10] = (far + near) / (near - far);
  out[11] = -1;
  out[14] = (2 * far * near) / (near - far);
  return out;
}

export function mat4LookAt(eye: Vec3, target: Vec3, up: Vec3 = { x: 0, y: 1, z: 0 }): Mat4 {
  let fx = target.x - eye.x, fy = target.y - eye.y, fz = target.z - eye.z;
  const fl = Math.hypot(fx, fy, fz);
  if (fl < 1e-9) throw new RangeError('Camera eye and target coincide');
  fx /= fl; fy /= fl; fz /= fl;
  let sx = fy * up.z - fz * up.y, sy = fz * up.x - fx * up.z, sz = fx * up.y - fy * up.x;
  const sl = Math.hypot(sx, sy, sz);
  if (sl < 1e-9) throw new RangeError('Camera looks along the up vector');
  sx /= sl; sy /= sl; sz /= sl;
  const ux = sy * fz - sz * fy, uy = sz * fx - sx * fz, uz = sx * fy - sy * fx;
  const out = new Float32Array(16);
  out[0] = sx; out[4] = sy; out[8] = sz;
  out[1] = ux; out[5] = uy; out[9] = uz;
  out[2] = -fx; out[6] = -fy; out[10] = -fz;
  out[12] = -(sx * eye.x + sy * eye.y + sz * eye.z);
  out[13] = -(ux * eye.x + uy * eye.y + uz * eye.z);
  out[14] = fx * eye.x + fy * eye.y + fz * eye.z;
  out[15] = 1;
  return out;
}

/** a * b, column-major (apply b first, then a). */
export function mat4Multiply(a: Mat4, b: Mat4): Mat4 {
  const out = new Float32Array(16);
  for (let col = 0; col < 4; col++) {
    for (let row = 0; row < 4; row++) {
      let sum = 0;
      for (let k = 0; k < 4; k++) sum += a[k * 4 + row] * b[col * 4 + k];
      out[col * 4 + row] = sum;
    }
  }
  return out;
}

export function viewMatrix(camera: CameraState): Mat4 {
  return mat4LookAt(cameraEye(camera), camera.target);
}

/** Clip planes follow the distance so a close room and a whole campus both keep depth precision. */
export function clipPlanes(camera: CameraState): { near: number; far: number } {
  return { near: Math.max(0.2, camera.distance * 0.04), far: Math.max(120, camera.distance * 6 + 200) };
}

export function viewProjection(camera: CameraState, aspect: number): Mat4 {
  const { near, far } = clipPlanes(camera);
  return mat4Multiply(mat4Perspective(camera.fov, aspect, near, far), viewMatrix(camera));
}

/** World point to normalised device coordinates (x, y in -1..1, +y up). */
export function projectToNdc(vp: Mat4, p: Vec3): ScreenPoint {
  const x = vp[0] * p.x + vp[4] * p.y + vp[8] * p.z + vp[12];
  const y = vp[1] * p.x + vp[5] * p.y + vp[9] * p.z + vp[13];
  const z = vp[2] * p.x + vp[6] * p.y + vp[10] * p.z + vp[14];
  const w = vp[3] * p.x + vp[7] * p.y + vp[11] * p.z + vp[15];
  if (w <= 1e-6) return { x: 0, y: 0, depth: w, visible: false };
  const nx = x / w, ny = y / w, nz = z / w;
  return { x: nx, y: ny, depth: w, visible: nz >= -1 && nz <= 1 };
}

/** NDC to CSS pixels inside a box of the given size (origin top left). */
export function ndcToPixels(point: { x: number; y: number }, width: number, height: number): { x: number; y: number } {
  return { x: (point.x * 0.5 + 0.5) * width, y: (0.5 - point.y * 0.5) * height };
}

/** The six frustum planes (left, right, bottom, top, near, far); a point is inside when n.p + d >= 0. */
export function frustumPlanes(vp: Mat4): Plane[] {
  const row = (r: number) => [vp[r], vp[4 + r], vp[8 + r], vp[12 + r]] as const;
  const r0 = row(0), r1 = row(1), r2 = row(2), r3 = row(3);
  const combine = (sign: 1 | -1, r: readonly number[]): Plane => {
    const raw = [r3[0] + sign * r[0], r3[1] + sign * r[1], r3[2] + sign * r[2], r3[3] + sign * r[3]];
    const len = Math.hypot(raw[0], raw[1], raw[2]) || 1;
    return [raw[0] / len, raw[1] / len, raw[2] / len, raw[3] / len];
  };
  return [combine(1, r0), combine(-1, r0), combine(1, r1), combine(-1, r1), combine(1, r2), combine(-1, r2)];
}

/** False only when the box is wholly outside one plane, so a visible object is never culled. */
export function aabbInFrustum(planes: readonly Plane[], box: Aabb): boolean {
  for (const [a, b, c, d] of planes) {
    const px = a >= 0 ? box.max.x : box.min.x;
    const py = b >= 0 ? box.max.y : box.min.y;
    const pz = c >= 0 ? box.max.z : box.min.z;
    if (a * px + b * py + c * pz + d < 0) return false;
  }
  return true;
}

/** Slab test. Returns the entry distance along the ray, or null on a miss. */
export function rayAabb(ray: Ray, box: Aabb): number | null {
  let tMin = 0, tMax = Infinity;
  const o = [ray.origin.x, ray.origin.y, ray.origin.z];
  const d = [ray.direction.x, ray.direction.y, ray.direction.z];
  const lo = [box.min.x, box.min.y, box.min.z];
  const hi = [box.max.x, box.max.y, box.max.z];
  for (let i = 0; i < 3; i++) {
    if (Math.abs(d[i]) < 1e-12) {
      if (o[i] < lo[i] || o[i] > hi[i]) return null;
      continue;
    }
    let t1 = (lo[i] - o[i]) / d[i], t2 = (hi[i] - o[i]) / d[i];
    if (t1 > t2) [t1, t2] = [t2, t1];
    tMin = Math.max(tMin, t1);
    tMax = Math.min(tMax, t2);
    if (tMin > tMax) return null;
  }
  return tMin;
}

export type PickTarget = Readonly<{ id: string; box: Aabb }>;

/** Nearest target along the ray, or null. Ties go to the first listed target. */
export function pickNearest(ray: Ray, targets: readonly PickTarget[]): { id: string; distance: number } | null {
  let best: { id: string; distance: number } | null = null;
  for (const target of targets) {
    const t = rayAabb(ray, target.box);
    if (t !== null && (best === null || t < best.distance)) best = { id: target.id, distance: t };
  }
  return best;
}

export function aabbCenter(box: Aabb): Vec3 {
  return { x: (box.min.x + box.max.x) / 2, y: (box.min.y + box.max.y) / 2, z: (box.min.z + box.max.z) / 2 };
}

export function mergeAabb(a: Aabb, b: Aabb): Aabb {
  return {
    min: { x: Math.min(a.min.x, b.min.x), y: Math.min(a.min.y, b.min.y), z: Math.min(a.min.z, b.min.z) },
    max: { x: Math.max(a.max.x, b.max.x), y: Math.max(a.max.y, b.max.y), z: Math.max(a.max.z, b.max.z) },
  };
}
