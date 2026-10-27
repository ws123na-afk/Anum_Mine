/**
 * The few models record-backed work motion adds to the scenery models (docs/ANUM_MINE_HQ_CAMPUS_MOTION.md): a box truck, a tyre rack, the roof beacon
 * and the pool of light a parked vehicle casts. The van and the tyre hauler are the scenery ones. Local frame: nose towards +z, wheels on y = 0.
 * No lettering, no plate, no brand, no number: a vehicle here is one representative of an event type, never a count.
 */
import { MeshBuilder, hex, scale, type Rgb } from '../../lib/hqGeometry.ts';
import type { SceneryVehicleParts } from './sceneryVehicle.ts';

const WHITE: Rgb = [1, 1, 1];
const GLASS = hex('#1d2a38');
const DARK = hex('#15191e');
const HEAD = hex('#fff1c9');
const TAIL = hex('#ff2e2e');
export const BOX_TRUCK_TRIANGLES_CAP = 160;
export const BOX_TRUCK_WHEELS: readonly (readonly [number, number])[] = [[-1.0, 3.1], [1.0, 3.1], [-1.0, -1.8], [1.0, -1.8], [-1.0, -3.2], [1.0, -3.2]];
export const BOX_TRUCK_WHEEL_RADIUS = 0.5;

let truckCache: SceneryVehicleParts | null = null;
/** A box truck: a painted cab (tinted per use), a pale box body, dark chassis and glass. */
export function boxTruckParts(): SceneryVehicleParts {
  if (truckCache) return truckCache;
  const body = new MeshBuilder(), trim = new MeshBuilder(), lights = new MeshBuilder();
  body.box(-1.1, 0.8, 2.3, 2.2, 1.55, 2.0, WHITE, { grad: 0.1 });
  body.box(-1.2, 1.0, -4.2, 2.4, 2.5, 6.4, hex('#e3e7ec'), { grad: 0.12 });
  trim.quad([-0.95, 1.55, 4.305], [0.95, 1.55, 4.305], [0.95, 2.25, 4.305], [-0.95, 2.25, 4.305], GLASS);
  trim.box(-0.9, 0.55, -4.3, 1.8, 0.3, 8.6, DARK);
  for (const sx of [-1, 1]) {
    lights.box(sx * 0.7 - 0.17, 1.0, 4.3, 0.34, 0.16, 0.06, HEAD, { emissive: 1, shade: false });
    lights.box(sx * 0.8 - 0.17, 0.95, -4.24, 0.34, 0.14, 0.06, TAIL, { emissive: 1, shade: false });
  }
  truckCache = { body: body.toFloat32(), trim: trim.toFloat32(), lights: lights.toFloat32(), triangles: body.triangleCount + trim.triangleCount + lights.triangleCount };
  return truckCache;
}

let beaconCache: Float32Array | null = null;
/** A small white block; the layer colours it amber, green or red and pulses it. */
export function beaconBlock(): Float32Array {
  if (beaconCache) return beaconCache;
  const b = new MeshBuilder();
  b.box(-0.2, 0, -0.2, 0.4, 0.28, 0.4, WHITE, { emissive: 1, shade: false });
  beaconCache = b.toFloat32();
  return beaconCache;
}

let poolCache: Float32Array | null = null;
/** A flat disc of radius 1, bright in the middle and black at the edge: drawn additively, tinted by the beacon colour, it lights the road round a parked vehicle. */
export function lightPool(): Float32Array {
  if (poolCache) return poolCache;
  const b = new MeshBuilder();
  const n = 20, mid: Rgb = [1, 1, 1], edge: Rgb = [0, 0, 0];
  for (let i = 0; i < n; i++) {
    const a0 = (i / n) * Math.PI * 2, a1 = ((i + 1) / n) * Math.PI * 2;
    b.triV([0, 0.12, 0], [Math.sin(a0), 0.12, Math.cos(a0)], [Math.sin(a1), 0.12, Math.cos(a1)], mid, edge, edge);
  }
  poolCache = b.toFloat32();
  return poolCache;
}

export const RACK_TRIANGLES_CAP = 100;
let rackCache: Float32Array | null = null;
/** A tyre rack, 3.2 m long along x and 0.9 m deep: two uprights, three rails and a plank floor. */
export function tyreRack(): Float32Array {
  if (rackCache) return rackCache;
  const b = new MeshBuilder();
  const steel = hex('#59636e'), wood = hex('#9a7a52');
  for (const x of [-1.5, 1.5]) for (const z of [-0.4, 0.4]) b.box(x - 0.05, 0, z - 0.05, 0.1, 1.5, 0.1, steel);
  for (const y of [0.35, 0.9, 1.45]) for (const z of [-0.4, 0.4]) b.box(-1.55, y, z - 0.04, 3.1, 0.08, 0.08, scale(steel, 1.15));
  b.box(-1.55, 0.1, -0.45, 3.1, 0.06, 0.9, wood);
  rackCache = b.toFloat32();
  return rackCache;
}
