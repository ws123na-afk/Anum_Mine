/**
 * The one low-poly vehicle the ambient traffic is instanced from, built as three parts so a renderer can colour
 * them separately: the painted body (white, tinted per instance), the dark glass, wheels and trim (fixed colour),
 * and the head and tail lights (emissive). Local frame: nose towards +z, wheels on y = 0, about 4.4 m long.
 * Sedan, SUV and van are the same part set scaled by `VEHICLE_SCALE`. No lettering, no plate, no brand.
 */
import { MeshBuilder, hex, scale, type Rgb } from '../../lib/hqGeometry.ts';
import type { VehicleKind } from './scenery.ts';

export const SCENERY_VEHICLE_TRIANGLES_CAP = 140;
/** Width, height and length factors per kind. */
export const VEHICLE_SCALE: Readonly<Record<VehicleKind, readonly [number, number, number]>> = { 0: [1, 1, 1], 1: [1.07, 1.18, 1.06], 2: [1.12, 1.55, 1.12], 3: [1.28, 2.05, 2.7], 4: [1, 1, 1] };

export type SceneryVehicleParts = Readonly<{ body: Float32Array; trim: Float32Array; lights: Float32Array; triangles: number }>;

const WHITE: Rgb = [1, 1, 1];
const GLASS = hex('#1d2a38');
const TYRE = hex('#15191e');
const HEAD = hex('#fff1c9');
const TAIL = hex('#ff2e2e');

let cached: SceneryVehicleParts | null = null;

export function sceneryVehicleParts(): SceneryVehicleParts {
  if (cached) return cached;
  const body = new MeshBuilder(), trim = new MeshBuilder(), lights = new MeshBuilder();
  const hw = 0.92, L = 2.2;
  // Painted lower body with a short bonnet and boot slope, and the roof.
  body.box(-hw, 0.3, -L, hw * 2, 0.5, L * 2, WHITE, { grad: 0.1 });
  body.quad([-hw, 0.8, L - 0.05], [hw, 0.8, L - 0.05], [hw * 0.92, 0.72, L], [-hw * 0.92, 0.72, L], WHITE);
  body.quad([-hw * 0.9, 0.8, 1.2], [hw * 0.9, 0.8, 1.2], [hw * 0.9, 0.8, L - 0.05], [-hw * 0.9, 0.8, L - 0.05], WHITE);
  body.quad([-hw * 0.9, 0.8, -L + 0.05], [hw * 0.9, 0.8, -L + 0.05], [hw * 0.9, 0.8, -1.3], [-hw * 0.9, 0.8, -1.3], WHITE);
  const zf = 0.95, zr = -1.2, zf2 = 0.42, zr2 = -0.88, y0 = 0.8, y1 = 1.36, wb = 0.82, wt = 0.7;
  body.quad([-wt, y1, zr2], [wt, y1, zr2], [wt, y1, zf2], [-wt, y1, zf2], WHITE);
  // Glass: windscreen, rear window and the two side windows of the cabin, wheels and the dark sill.
  trim.quad([-wb, y0, zf], [wb, y0, zf], [wt, y1, zf2], [-wt, y1, zf2], GLASS);
  trim.quad([wb, y0, zr], [-wb, y0, zr], [-wt, y1, zr2], [wt, y1, zr2], GLASS);
  trim.quad([wb, y0, zf], [wb, y0, zr], [wt, y1, zr2], [wt, y1, zf2], GLASS);
  trim.quad([-wb, y0, zr], [-wb, y0, zf], [-wt, y1, zf2], [-wt, y1, zr2], GLASS);
  // The wheels are their own instanced part (`sceneryWheel`), so they can turn.
  trim.box(-hw, 0.22, -L, hw * 2, 0.1, L * 2, TYRE);
  // Lights: two at the nose, two at the tail.
  for (const sx of [-1, 1]) {
    lights.box(sx * 0.58 - 0.17, 0.5, L - 0.02, 0.34, 0.14, 0.06, HEAD, { emissive: 1, shade: false });
    lights.box(sx * 0.62 - 0.17, 0.54, -L - 0.04, 0.34, 0.12, 0.06, TAIL, { emissive: 1, shade: false });
  }
  const triangles = body.triangleCount + trim.triangleCount + lights.triangleCount;
  cached = { body: body.toFloat32(), trim: trim.toFloat32(), lights: lights.toFloat32(), triangles };
  return cached;
}

// ---------------------------------------------------------------- wheels, beams, the tyre hauler

/** Wheel radius per kind (metres) and where the wheels sit: lateral x and z in the vehicle's local frame, nose towards +z. */
export const WHEEL_RADIUS: Readonly<Record<VehicleKind, number>> = { 0: 0.33, 1: 0.38, 2: 0.4, 3: 0.52, 4: 0.52 };
const car = (k: VehicleKind): readonly (readonly [number, number])[] => {
  const [sw, , sl] = VEHICLE_SCALE[k];
  const x = 0.92 * sw - 0.02, z = 1.35 * sl;
  return [[-x, z], [x, z], [-x, -z], [x, -z]];
};
export const WHEEL_SPOTS: Readonly<Record<VehicleKind, readonly (readonly [number, number])[]>> = {
  0: car(0), 1: car(1), 2: car(2), 3: car(3),
  4: [[-1.0, 3.2], [1.0, 3.2], [-1.0, -2.2], [1.0, -2.2], [-1.0, -3.6], [1.0, -3.6]],
};
export const SCENERY_WHEEL_TRIANGLES_CAP = 60;

let wheelCache: Float32Array | null = null;
/** One wheel of radius 1 and width 0.7 along x, centred on the origin: a dark tyre with three light spokes on each face, so turning is visible. */
export function sceneryWheel(): Float32Array {
  if (wheelCache) return wheelCache;
  const b = new MeshBuilder();
  const n = 10, hw = 0.35, spoke = hex('#b7c0c9'), rim = scale(TYRE, 1.35);
  const ring = (x: number, a: number, r: number): [number, number, number] => [x, Math.sin(a) * r, Math.cos(a) * r];
  for (let i = 0; i < n; i++) {
    const a0 = (i / n) * Math.PI * 2, a1 = ((i + 1) / n) * Math.PI * 2;
    b.quad(ring(-hw, a0, 1), ring(hw, a0, 1), ring(hw, a1, 1), ring(-hw, a1, 1), TYRE);
    for (const sx of [-1, 1]) b.tri([sx * hw, 0, 0], ring(sx * hw, sx > 0 ? a1 : a0, 1), ring(sx * hw, sx > 0 ? a0 : a1, 1), rim);
  }
  for (const sx of [-1, 1]) for (let k = 0; k < 3; k++) {
    const a = (k / 3) * Math.PI * 2, w = 0.11, x = sx * (hw + 0.012);
    const p = (r: number, off: number): [number, number, number] => [x, Math.sin(a) * r + Math.cos(a) * off, Math.cos(a) * r - Math.sin(a) * off];
    if (sx > 0) b.quad(p(0.1, w), p(0.82, w), p(0.82, -w), p(0.1, -w), spoke);
    else b.quad(p(0.1, -w), p(0.82, -w), p(0.82, w), p(0.1, w), spoke);
  }
  wheelCache = b.toFloat32();
  return wheelCache;
}

let beamCache: Float32Array | null = null;
/** The pool of light a vehicle's headlights throw on the road: one flat trapezoid ahead of the nose, bright near and black far (drawn additively). */
export function sceneryBeam(): Float32Array {
  if (beamCache) return beamCache;
  const b = new MeshBuilder();
  const near = hex('#ffe9b0'), far: Rgb = [0, 0, 0];
  b.quadV([-0.8, 0.1, 2.3], [-2.1, 0.1, 9.5], [2.1, 0.1, 9.5], [0.8, 0.1, 2.3], near, far, far, near);
  beamCache = b.toFloat32();
  return beamCache;
}

export const HAULER_TRIANGLES_CAP = 900;
let haulerCache: SceneryVehicleParts | null = null;
/**
 * A flatbed lorry carrying stacks of tyres, as plain scenery: a cab, a bed and twenty four tyres lying in stacks. No lettering, no plate, no brand,
 * and nothing about it says an order, a delivery or a job. Same three parts as the cars so one layer draws it.
 */
export function haulerParts(): SceneryVehicleParts {
  if (haulerCache) return haulerCache;
  const body = new MeshBuilder(), trim = new MeshBuilder(), lights = new MeshBuilder();
  const w = 1.15;
  body.box(-w, 0.8, 2.3, w * 2, 1.7, 2.1, WHITE, { grad: 0.1 });
  body.box(-w * 0.95, 2.5, 2.4, w * 1.9, 0.12, 1.9, WHITE);
  trim.quad([-w * 0.85, 1.6, 4.405], [w * 0.85, 1.6, 4.405], [w * 0.85, 2.35, 4.405], [-w * 0.85, 2.35, 4.405], GLASS);
  trim.box(-0.9, 0.55, -5.1, 1.8, 0.3, 9.5, TYRE);
  trim.box(-w, 0.85, -5.0, w * 2, 0.16, 7.2, hex('#59636e'));
  const tyre = hex('#1c2127');
  for (const cx of [-0.58, 0.58]) for (let r = 0; r < 4; r++) for (let h = 0; h < 3; h++) {
    trim.prism(cx, 1.01 + h * 0.3, 1.0 - r * 1.5, 0.55, 0.55, 0.28, 8, h % 2 ? scale(tyre, 1.2) : tyre);
  }
  for (const sx of [-1, 1]) {
    lights.box(sx * 0.7 - 0.17, 1.0, 4.4, 0.34, 0.16, 0.06, HEAD, { emissive: 1, shade: false });
    lights.box(sx * 0.8 - 0.17, 0.95, -5.12, 0.34, 0.14, 0.06, TAIL, { emissive: 1, shade: false });
  }
  haulerCache = { body: body.toFloat32(), trim: trim.toFloat32(), lights: lights.toFloat32(), triangles: body.triangleCount + trim.triangleCount + lights.triangleCount };
  return haulerCache;
}
