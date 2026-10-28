/**
 * Which way a campus building faces (docs/ANUM_MINE_HQ_CENTER_PLAZA.md, "Buildings face the plaza").
 *
 * Every exterior asset is built with its front (entrance, sign, forecourt) on +z and is turned by quarter turns
 * (`ExteriorParams.rotation`). A campus building takes the quarter turn whose front points most directly at the plaza
 * centre, so the door, the sign and the board look at the fountain. Quarter turns keep every footprint, lot, hit box and
 * walkway obstacle an axis aligned rectangle, which the layout, the walkway router, the pick boxes and the plan atlas rely on.
 * Pure maths, no DOM: the unit runner covers it.
 */

/** Quarter turns: 0 front faces +z, 1 faces -x, 2 faces -z, 3 faces +x (the exterior kit's convention). */
export type QuarterTurn = 0 | 1 | 2 | 3;

/** The outward direction of the front after `turn` quarter turns (unit vector on the ground). */
export function frontOf(turn: QuarterTurn): readonly [number, number] {
  return turn === 0 ? [0, 1] : turn === 1 ? [-1, 0] : turn === 2 ? [0, -1] : [1, 0];
}

/**
 * The quarter turn that makes a building at (x, z) face the point (cx, cz): the side whose outward normal is closest to the
 * direction to the centre. On an exact diagonal the z axis wins (a stable tie break); a building on the centre faces +z.
 */
export function facingTurn(x: number, z: number, cx = 0, cz = 0): QuarterTurn {
  const dx = cx - x, dz = cz - z;
  if (Math.abs(dx) < 1e-9 && Math.abs(dz) < 1e-9) return 0;
  if (Math.abs(dz) >= Math.abs(dx) - 1e-9) return dz > 0 ? 0 : 2;
  return dx < 0 ? 1 : 3;
}

/** The exact heading (radians, `atan2(x, z)` of the direction) from (x, z) to the centre; the quarter turn is the nearest of four. */
export function facingAngle(x: number, z: number, cx = 0, cz = 0): number {
  return Math.atan2(cx - x, cz - z);
}

/** Heading of a quarter turn's front, in the same convention as `facingAngle`. */
export const turnAngle = (turn: QuarterTurn): number => [0, -Math.PI / 2, Math.PI, Math.PI / 2][turn];

/** Turns a local point (x, z) by quarter turns (the exterior kit's rotation: each turn maps (x, z) to (-z, x)). */
export function turnXZ(x: number, z: number, turn: number): [number, number] {
  let a = x, b = z;
  for (let k = 0; k < (((turn % 4) + 4) % 4); k++) [a, b] = [-b + 0, a + 0];
  return [a, b];
}

/** The inverse of `turnXZ`: a world offset back into the building's own frame. */
export const unturnXZ = (x: number, z: number, turn: number): [number, number] => turnXZ(x, z, 4 - ((((turn % 4) + 4) % 4)));

export type Rect = Readonly<{ x0: number; z0: number; x1: number; z1: number }>;

/** A rectangle given in the building's own frame (front on +z) turned and moved to the building's world position. */
export function turnRect(local: Rect, turn: number, ox: number, oz: number): Rect {
  const [ax, az] = turnXZ(local.x0, local.z0, turn), [bx, bz] = turnXZ(local.x1, local.z1, turn);
  return { x0: Math.min(ax, bx) + ox, z0: Math.min(az, bz) + oz, x1: Math.max(ax, bx) + ox, z1: Math.max(az, bz) + oz };
}

/** The ground size a footprint covers in world axes: width and depth swap on an odd number of turns. */
export function worldSize(p: Readonly<{ width: number; depth: number; turn?: number }>): { w: number; d: number } {
  return (p.turn ?? 0) % 2 === 1 ? { w: p.depth, d: p.width } : { w: p.width, d: p.depth };
}

/** The world rectangle of a footprint centred at (x, z). */
export function footprintRect(p: Readonly<{ x: number; z: number; width: number; depth: number; turn?: number }>): Rect {
  const s = worldSize(p);
  return { x0: p.x - s.w / 2, z0: p.z - s.d / 2, x1: p.x + s.w / 2, z1: p.z + s.d / 2 };
}

/** VERTEX_FLOATS of the shared vertex layout (position 3, normal 3, ...): kept here so this module imports nothing. */
const STRIDE = 13;

/** Turns a vertex buffer (positions and normals) by quarter turns and moves it to (ox, oz), in place. */
export function turnVertices(data: Float32Array, turn: number, ox: number, oz: number): void {
  for (let i = 0; i < data.length; i += STRIDE) {
    const [x, z] = turnXZ(data[i], data[i + 2], turn), [nx, nz] = turnXZ(data[i + 3], data[i + 5], turn);
    data[i] = x + ox; data[i + 2] = z + oz; data[i + 3] = nx; data[i + 5] = nz;
  }
}

/** Turns a halo list (x, y, z, radius, r, g, b, strength per halo) the same way. */
export function turnHalos(list: readonly number[], turn: number, ox: number, oz: number): number[] {
  const out = list.slice();
  for (let i = 0; i < out.length; i += 8) {
    const [x, z] = turnXZ(out[i], out[i + 2], turn);
    out[i] = x + ox; out[i + 2] = z + oz;
  }
  return out;
}

/** Shortest yaw (degrees) from `from` to a heading in degrees, so a camera turns the short way round. */
export function nearestYaw(from: number, toDeg: number): number {
  const d = ((((toDeg - from) % 360) + 540) % 360) - 180;
  return from + d;
}

/** Degrees the focus camera stands off the front's axis: a three quarter view shows the front and one side. */
export const FRONT_YAW_OFFSET = 20;
