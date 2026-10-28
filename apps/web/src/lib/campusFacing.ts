/**
 * Which way a campus building faces (docs/ANUM_MINE_HQ_CENTER_PLAZA.md, "Buildings face the plaza").
 *
 * Every exterior asset is built with its front (entrance, sign, forecourt) on +z and is turned by quarter turns
 * (`ExteriorParams.rotation`). A campus building takes the quarter turn whose front points most directly at the plaza
 * centre, so the door, the sign and the board look at the fountain. Quarter turns keep every footprint, lot, hit box and
 * walkway obstacle an axis aligned rectangle, which the layout, the walkway router, the pick boxes and the plan atlas rely on.
 * A quarter turn alone can leave the front up to 45 degrees off the fountain (a building on a diagonal), so a building also takes a
 * small `yaw` (`leanOf`, at most 45 degrees either way) on top of its quarter turn: the front then points straight at the centre.
 * Rectangles of a leaned building are given as the axis aligned box round the turned corners (`turnRect`, `footprintRect`), the
 * walkway router tests the leaned footprint itself, and the glass lookup undoes the whole angle (`headingDelta`).
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

/**
 * The lean of a building at (x, z) with quarter turn `turn`: the small extra angle (radians, in [-pi/4, pi/4]) that turns its front
 * from the quarter turn's heading to the exact direction of the centre. Positive turns the heading the way `facingAngle` grows.
 */
export function leanOf(x: number, z: number, turn: QuarterTurn, cx = 0, cz = 0): number {
  if (Math.abs(cx - x) < 1e-9 && Math.abs(cz - z) < 1e-9) return 0;
  const d = facingAngle(x, z, cx, cz) - turnAngle(turn);
  return Math.atan2(Math.sin(d), Math.cos(d));
}

/** Turns an offset (x, z) so its heading grows by `a` radians (heading as `facingAngle`: atan2(x, z)). */
export function rotateXZ(x: number, z: number, a: number): [number, number] {
  if (!a) return [x, z];
  const c = Math.cos(a), s = Math.sin(a);
  return [x * c + z * s + 0, -x * s + z * c + 0];
}

/** The whole change of heading of a building's frame: its quarter turns (a quarter turn is -pi/2) plus its lean. */
export const headingDelta = (turn: number, yaw = 0): number => -((((turn % 4) + 4) % 4) * Math.PI) / 2 + yaw;

/** Turns a local point (x, z) by quarter turns (the exterior kit's rotation: each turn maps (x, z) to (-z, x)), then by the lean `yaw`. */
export function turnXZ(x: number, z: number, turn: number, yaw = 0): [number, number] {
  let a = x, b = z;
  for (let k = 0; k < (((turn % 4) + 4) % 4); k++) [a, b] = [-b + 0, a + 0];
  return yaw ? rotateXZ(a, b, yaw) : [a, b];
}

/** The inverse of `turnXZ`: a world offset back into the building's own frame. */
export const unturnXZ = (x: number, z: number, turn: number, yaw = 0): [number, number] => {
  const [a, b] = yaw ? rotateXZ(x, z, -yaw) : [x, z];
  return turnXZ(a, b, 4 - ((((turn % 4) + 4) % 4)));
};

export type Rect = Readonly<{ x0: number; z0: number; x1: number; z1: number }>;

/**
 * A rectangle given in the building's own frame (front on +z) turned and moved to the building's world position. With a lean it is the
 * axis aligned box round the four turned corners (it holds the leaned rectangle).
 */
export function turnRect(local: Rect, turn: number, ox: number, oz: number, yaw = 0): Rect {
  if (!yaw) {
    const [ax, az] = turnXZ(local.x0, local.z0, turn), [bx, bz] = turnXZ(local.x1, local.z1, turn);
    return { x0: Math.min(ax, bx) + ox, z0: Math.min(az, bz) + oz, x1: Math.max(ax, bx) + ox, z1: Math.max(az, bz) + oz };
  }
  let x0 = Infinity, z0 = Infinity, x1 = -Infinity, z1 = -Infinity;
  for (const [lx, lz] of [[local.x0, local.z0], [local.x1, local.z0], [local.x1, local.z1], [local.x0, local.z1]] as const) {
    const [x, z] = turnXZ(lx, lz, turn, yaw);
    x0 = Math.min(x0, x); z0 = Math.min(z0, z); x1 = Math.max(x1, x); z1 = Math.max(z1, z);
  }
  return { x0: x0 + ox, z0: z0 + oz, x1: x1 + ox, z1: z1 + oz };
}

/** The ground size a footprint covers in world axes: width and depth swap on an odd number of turns; a lean gives the box round it. */
export function worldSize(p: Readonly<{ width: number; depth: number; turn?: number; yaw?: number }>): { w: number; d: number } {
  const base = (p.turn ?? 0) % 2 === 1 ? { w: p.depth, d: p.width } : { w: p.width, d: p.depth };
  if (!p.yaw) return base;
  const c = Math.abs(Math.cos(p.yaw)), s = Math.abs(Math.sin(p.yaw));
  return { w: base.w * c + base.d * s, d: base.w * s + base.d * c };
}

/** The world rectangle of a footprint centred at (x, z). */
export function footprintRect(p: Readonly<{ x: number; z: number; width: number; depth: number; turn?: number; yaw?: number }>): Rect {
  const s = worldSize(p);
  return { x0: p.x - s.w / 2, z0: p.z - s.d / 2, x1: p.x + s.w / 2, z1: p.z + s.d / 2 };
}

/** VERTEX_FLOATS of the shared vertex layout (position 3, normal 3, ...): kept here so this module imports nothing. */
const STRIDE = 13;

/** Turns a vertex buffer (positions and normals) by quarter turns and the lean, and moves it to (ox, oz), in place. */
export function turnVertices(data: Float32Array, turn: number, ox: number, oz: number, yaw = 0): void {
  for (let i = 0; i < data.length; i += STRIDE) {
    const [x, z] = turnXZ(data[i], data[i + 2], turn, yaw), [nx, nz] = turnXZ(data[i + 3], data[i + 5], turn, yaw);
    data[i] = x + ox; data[i + 2] = z + oz; data[i + 3] = nx; data[i + 5] = nz;
  }
}

/** Turns a halo list (x, y, z, radius, r, g, b, strength per halo) the same way. */
export function turnHalos(list: ArrayLike<number>, turn: number, ox: number, oz: number, yaw = 0): number[] {
  const out = Array.from(list);
  for (let i = 0; i < out.length; i += 8) {
    const [x, z] = turnXZ(out[i], out[i + 2], turn, yaw);
    out[i] = x + ox; out[i + 2] = z + oz;
  }
  return out;
}

/**
 * Leans what is already placed in the world (an exterior asset built with its quarter turn round (cx, cz)): vertex positions and normals
 * of a buffer, in place, turned by `yaw` round the building's middle.
 */
export function leanVertices(data: Float32Array, yaw: number, cx: number, cz: number): void {
  if (!yaw) return;
  const c = Math.cos(yaw), s = Math.sin(yaw);
  for (let i = 0; i < data.length; i += STRIDE) {
    const x = data[i] - cx, z = data[i + 2] - cz, nx = data[i + 3], nz = data[i + 5];
    data[i] = x * c + z * s + cx; data[i + 2] = -x * s + z * c + cz;
    data[i + 3] = nx * c + nz * s; data[i + 5] = -nx * s + nz * c;
  }
}

/** One placed point leaned by `yaw` round (cx, cz). */
export function leanPoint<T extends Readonly<{ x: number; z: number }>>(p: T, yaw: number, cx: number, cz: number): T {
  if (!yaw) return p;
  const [x, z] = rotateXZ(p.x - cx, p.z - cz, yaw);
  return { ...p, x: x + cx, z: z + cz };
}

/** Shortest yaw (degrees) from `from` to a heading in degrees, so a camera turns the short way round. */
export function nearestYaw(from: number, toDeg: number): number {
  const d = ((((toDeg - from) % 360) + 540) % 360) - 180;
  return from + d;
}

/** Degrees the focus camera stands off the front's axis: a three quarter view shows the front and one side. */
export const FRONT_YAW_OFFSET = 20;
