/**
 * The one low-poly vehicle the ambient traffic is instanced from, built as three parts so a renderer can colour
 * them separately: the painted body (white, tinted per instance), the dark glass, wheels and trim (fixed colour),
 * and the head and tail lights (emissive). Local frame: nose towards +z, wheels on y = 0, about 4.4 m long.
 * Sedan, SUV and van are the same part set scaled by `VEHICLE_SCALE`. No lettering, no plate, no brand.
 */
import { MeshBuilder, hex, type Rgb } from '../../lib/hqGeometry.ts';
import type { VehicleKind } from './ambient.ts';

export const AMBIENT_VEHICLE_TRIANGLES_CAP = 140;
/** Width, height and length factors per kind. */
export const VEHICLE_SCALE: Readonly<Record<VehicleKind, readonly [number, number, number]>> = { 0: [1, 1, 1], 1: [1.07, 1.18, 1.06], 2: [1.12, 1.55, 1.12] };

export type AmbientVehicleParts = Readonly<{ body: Float32Array; trim: Float32Array; lights: Float32Array; triangles: number }>;

const WHITE: Rgb = [1, 1, 1];
const GLASS = hex('#1d2a38');
const TYRE = hex('#15191e');
const HEAD = hex('#fff1c9');
const TAIL = hex('#ff2e2e');

let cached: AmbientVehicleParts | null = null;

export function ambientVehicleParts(): AmbientVehicleParts {
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
  for (const sx of [-1, 1]) for (const wz of [-1.35, 1.35]) trim.box(sx * hw - 0.1, 0, wz - 0.33, 0.2, 0.66, 0.66, TYRE);
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
