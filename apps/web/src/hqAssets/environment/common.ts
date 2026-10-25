/**
 * Shared building blocks for the campus environment assets: a rotated and scaled drawing frame,
 * glow points for night lighting, instancing templates and a few small helpers.
 *
 * Everything is decoration generated from a seed. No name, count or status is encoded in any
 * geometry, and nothing here draws people.
 */
import { MeshBuilder, VERTEX_FLOATS, scale, seeded, type Rgb } from '../../lib/hqGeometry.ts';
/** The renderer draws a real shadow map; baked ground shadows would double them. */
const BAKED_SHADOWS = false;
import { SHADOW, SHADOW_DX, SHADOW_DZ, flatPoly } from '../../lib/hqProps.ts';
import type { Aabb } from '../../lib/hqMath.ts';

export { seeded };
export type Vec3t = readonly [number, number, number];

/** A point light for night scenes. The renderer may draw a halo or a light pool for each one. */
export type GlowPoint = Readonly<{ x: number; y: number; z: number; radius: number; color: Rgb; intensity: number }>;

/** What every builder returns: the triangles it added, its night lights and its bounds. */
export type AssetResult = Readonly<{ triangles: number; glow: readonly GlowPoint[]; bounds: Aabb }>;

export type BoxFace = 'top' | 'bottom' | 'front' | 'back' | 'left' | 'right';
export type BoxOptions = { emissive?: number; skip?: readonly BoxFace[]; grad?: number; flat?: boolean };

const BOX_SHADE: Record<BoxFace, number> = { top: 1, bottom: 0.55, front: 0.8, back: 0.76, left: 0.68, right: 0.9 };

/**
 * Builds an asset into a scratch builder, appends it to `b` and reports triangles and exact bounds.
 * `draw` receives the scratch builder; push night lights into `glow` while drawing.
 */
export function scoped(b: MeshBuilder, glow: GlowPoint[], draw: (m: MeshBuilder) => void): AssetResult {
  const m = new MeshBuilder();
  draw(m);
  appendMesh(b, m);
  return { triangles: m.triangleCount, glow, bounds: boundsWithoutShadows(m) };
}

/** Bounds of the solid geometry: flat ground shadows (the `SHADOW` colour near the ground) are left out. */
export function boundsWithoutShadows(m: MeshBuilder): Aabb {
  const v = m.toFloat32();
  const lo = [Infinity, Infinity, Infinity], hi = [-Infinity, -Infinity, -Infinity];
  for (let i = 0; i + 3 * VERTEX_FLOATS <= v.length; i += 3 * VERTEX_FLOATS) {
    let shadow = true;
    for (let k = 0; k < 3; k++) {
      const o = i + k * VERTEX_FLOATS;
      if (!(v[o + 1] < 0.031 && Math.abs(v[o + 6] - SHADOW[0]) < 1e-4 && Math.abs(v[o + 7] - SHADOW[1]) < 1e-4 && Math.abs(v[o + 8] - SHADOW[2]) < 1e-4)) { shadow = false; break; }
    }
    if (shadow) continue;
    for (let k = 0; k < 3; k++) for (let a = 0; a < 3; a++) {
      const val = v[i + k * VERTEX_FLOATS + a];
      if (val < lo[a]) lo[a] = val;
      if (val > hi[a]) hi[a] = val;
    }
  }
  if (lo[0] === Infinity) return m.bounds();
  return { min: { x: lo[0], y: lo[1], z: lo[2] }, max: { x: hi[0], y: hi[1], z: hi[2] } };
}

/** Copies every triangle of `from` into `into`. */
export function appendMesh(into: MeshBuilder, from: MeshBuilder): void {
  into.append(from.toFloat32());
}

function norm3(a: Vec3t, b: Vec3t, c: Vec3t): Vec3t {
  const ux = b[0] - a[0], uy = b[1] - a[1], uz = b[2] - a[2];
  const vx = c[0] - a[0], vy = c[1] - a[1], vz = c[2] - a[2];
  const nx = uy * vz - uz * vy, ny = uz * vx - ux * vz, nz = ux * vy - uy * vx;
  const l = Math.hypot(nx, ny, nz) || 1;
  return [nx / l, ny / l, nz / l];
}

/**
 * Draws in a local frame: origin (ox, oy, oz), heading `rot` (radians from +z towards +x) and a
 * uniform scale. Local +z is "forward", +x is to the right of a driver heading forward (west for
 * rot 0 when seen from above with z south, which only matters for symmetry), +y is up.
 * Every solid is wound outward by testing against an interior reference point, so assets never
 * depend on hand-checked winding.
 */
export class Frame {
  readonly b: MeshBuilder;
  readonly ox: number;
  readonly oy: number;
  readonly oz: number;
  readonly rot: number;
  readonly s: number;
  private readonly cs: number;
  private readonly sn: number;
  constructor(b: MeshBuilder, ox = 0, oy = 0, oz = 0, rot = 0, s = 1) {
    this.b = b; this.ox = ox; this.oy = oy; this.oz = oz; this.rot = rot; this.s = s;
    this.cs = Math.cos(rot);
    this.sn = Math.sin(rot);
  }

  p(x: number, y: number, z: number): Vec3t {
    return [this.ox + this.s * (x * this.cs + z * this.sn), this.oy + this.s * y, this.oz + this.s * (-x * this.sn + z * this.cs)];
  }

  /** A quad whose normal faces away from `ref` (a local point inside the solid). */
  face(pts: readonly Vec3t[], ref: Vec3t, cols: readonly Rgb[], emissive = 0): void {
    const w = pts.map((q) => this.p(q[0], q[1], q[2]));
    const r = this.p(ref[0], ref[1], ref[2]);
    const n = norm3(w[0], w[1], w[2]);
    const mx = (w[0][0] + w[1][0] + w[2][0] + w[3][0]) / 4 - r[0];
    const my = (w[0][1] + w[1][1] + w[2][1] + w[3][1]) / 4 - r[1];
    const mz = (w[0][2] + w[1][2] + w[2][2] + w[3][2]) / 4 - r[2];
    const flip = n[0] * mx + n[1] * my + n[2] * mz < 0;
    const [a, bb, c, d] = flip ? [w[0], w[3], w[2], w[1]] : [w[0], w[1], w[2], w[3]];
    const [ca, cb, cc, cd] = flip ? [cols[0], cols[3], cols[2], cols[1]] : [cols[0], cols[1], cols[2], cols[3]];
    this.b.quadV(a, bb, c, d, ca, cb, cc, cd, emissive);
  }

  /** Triangle facing towards `dir` (local vector). */
  triToward(a: Vec3t, b2: Vec3t, c: Vec3t, color: Rgb, dir: Vec3t, emissive = 0): void {
    const w = [a, b2, c].map((q) => this.p(q[0], q[1], q[2]));
    const o = this.p(0, 0, 0), t = this.p(dir[0], dir[1], dir[2]);
    const n = norm3(w[0], w[1], w[2]);
    const flip = n[0] * (t[0] - o[0]) + n[1] * (t[1] - o[1]) + n[2] * (t[2] - o[2]) < 0;
    if (flip) this.b.tri(w[0], w[2], w[1], color, emissive);
    else this.b.tri(w[0], w[1], w[2], color, emissive);
  }

  /** A quad facing towards `dir` (local vector). */
  quadToward(a: Vec3t, b2: Vec3t, c: Vec3t, d: Vec3t, color: Rgb, dir: Vec3t, emissive = 0): void {
    this.triToward(a, b2, c, color, dir, emissive);
    this.triToward(a, c, d, color, dir, emissive);
  }

  /** Box from its local minimum corner. */
  box(x: number, y: number, z: number, w: number, h: number, d: number, color: Rgb, o: BoxOptions = {}): void {
    const x1 = x + w, y1 = y + h, z1 = z + d;
    const ref: Vec3t = [x + w / 2, y + h / 2, z + d / 2];
    const skip = new Set(o.skip ?? []);
    const em = o.emissive ?? 0;
    const grad = o.grad ?? 0;
    const shade = (f: BoxFace): Rgb => (o.flat ? color : scale(color, BOX_SHADE[f]));
    const sideCols = (f: BoxFace, lowFirst: readonly [boolean, boolean, boolean, boolean]): Rgb[] => {
      const top = shade(f), bot = grad > 0 ? scale(top, 1 - grad) : top;
      return lowFirst.map((low) => (low ? bot : top));
    };
    const one = (f: BoxFace, c: Rgb): Rgb[] => [c, c, c, c];
    if (!skip.has('top')) this.face([[x, y1, z], [x, y1, z1], [x1, y1, z1], [x1, y1, z]], ref, one('top', shade('top')), em);
    if (!skip.has('bottom')) this.face([[x, y, z], [x1, y, z], [x1, y, z1], [x, y, z1]], ref, one('bottom', shade('bottom')), em);
    if (!skip.has('back')) this.face([[x, y, z], [x, y1, z], [x1, y1, z], [x1, y, z]], ref, sideCols('back', [true, false, false, true]), em);
    if (!skip.has('front')) this.face([[x1, y, z1], [x1, y1, z1], [x, y1, z1], [x, y, z1]], ref, sideCols('front', [true, false, false, true]), em);
    if (!skip.has('left')) this.face([[x, y, z1], [x, y1, z1], [x, y1, z], [x, y, z]], ref, sideCols('left', [true, false, false, true]), em);
    if (!skip.has('right')) this.face([[x1, y, z], [x1, y1, z], [x1, y1, z1], [x1, y, z1]], ref, sideCols('right', [true, false, false, true]), em);
  }

  /**
   * Cylinder or cone along an axis. (x, y, z) is the centre of the start cap; `len` runs along the
   * axis. Radii may differ at the ends.
   */
  cyl(
    x: number, y: number, z: number, axis: 'x' | 'y' | 'z', r0: number, r1: number, len: number, sides: number, color: Rgb,
    o: { emissive?: number; capStart?: boolean; capEnd?: boolean; capColor?: Rgb } = {},
  ): void {
    const n = Math.max(3, Math.floor(sides));
    const em = o.emissive ?? 0;
    const at = (t: number, r: number, i: number): Vec3t => {
      const a = (i / n) * Math.PI * 2, c = Math.cos(a) * r, s = Math.sin(a) * r;
      if (axis === 'x') return [x + t, y + c, z + s];
      if (axis === 'y') return [x + c, y + t, z + s];
      return [x + c, y + s, z + t];
    };
    const mid = (t: number): Vec3t => (axis === 'x' ? [x + t, y, z] : axis === 'y' ? [x, y + t, z] : [x, y, z + t]);
    for (let i = 0; i < n; i++) {
      const j = i + 1;
      const sh = scale(color, 0.7 + 0.3 * Math.cos(((i + 0.5) / n) * Math.PI * 2 - 0.9));
      const p0 = at(0, r0, i), p1 = at(0, r0, j), q1 = at(len, r1, j), q0 = at(len, r1, i);
      if (r1 <= 1e-6) this.faceTri(p0, p1, q0, mid(len / 2), sh, em);
      else this.face([p0, p1, q1, q0], mid(len / 2), [sh, sh, sh, sh], em);
      const cc = o.capColor ?? scale(color, 1.05);
      if (o.capStart) this.faceTri(mid(0), p0, p1, mid(len), cc, em);
      if (o.capEnd && r1 > 1e-6) this.faceTri(mid(len), q0, q1, mid(0), cc, em);
    }
  }

  private faceTri(a: Vec3t, b2: Vec3t, c: Vec3t, ref: Vec3t, color: Rgb, em: number): void {
    const w = [a, b2, c].map((q) => this.p(q[0], q[1], q[2]));
    const r = this.p(ref[0], ref[1], ref[2]);
    const n = norm3(w[0], w[1], w[2]);
    const flip = n[0] * ((w[0][0] + w[1][0] + w[2][0]) / 3 - r[0]) + n[1] * ((w[0][1] + w[1][1] + w[2][1]) / 3 - r[1]) + n[2] * ((w[0][2] + w[1][2] + w[2][2]) / 3 - r[2]) < 0;
    if (flip) this.b.tri(w[0], w[2], w[1], color, em);
    else this.b.tri(w[0], w[1], w[2], color, em);
  }

  /** Low-poly blob in local space (delegates to the shared builder with the frame offset). */
  blob(x: number, y: number, z: number, rx: number, ry: number, rz: number, color: Rgb, segments = 7): void {
    const q = this.p(x, y, z);
    this.b.blob(q[0], q[1], q[2], rx * this.s, ry * this.s, rz * this.s, color, segments);
  }

  /** A footprint-swept ground shadow for a local rectangle of height h. */
  shadow(x: number, z: number, w: number, d: number, h: number, y = 0.026): void {
    const corners = [this.p(x, 0, z), this.p(x + w, 0, z), this.p(x + w, 0, z + d), this.p(x, 0, z + d)].map((c) => [c[0], c[2]] as [number, number]);
    castShadowPoly(this.b, corners, h * this.s, y);
  }
}

/** Ground shadow of any convex footprint: the footprint plus the strips swept along the sun direction. */
export function castShadowPoly(b: MeshBuilder, corners: ReadonlyArray<readonly [number, number]>, h: number, y = 0.026, color: Rgb = SHADOW): void {
  if (!BAKED_SHADOWS) return;
  const vx = SHADOW_DX * h, vz = SHADOW_DZ * h;
  const moved = corners.map((c) => [c[0] + vx, c[1] + vz] as [number, number]);
  flatPoly(b, moved, y, color);
  for (let i = 0; i < corners.length; i++) {
    const j = (i + 1) % corners.length;
    flatPoly(b, [corners[i], corners[j], moved[j], moved[i]], y, color);
  }
}

// ---------------------------------------------------------------- instancing

/** A mesh built once and stamped many times. `vertices` uses the shared vertex layout. */
export type Template = Readonly<{ id: string; vertices: Float32Array; triangles: number }>;

/** One placement of a template: position, heading (radians) and uniform scale. */
export type Placement = Readonly<{ x: number; y?: number; z: number; rot?: number; scale?: number }>;

export function makeTemplate(id: string, build: (b: MeshBuilder) => void): Template {
  const b = new MeshBuilder();
  build(b);
  return { id, vertices: b.toFloat32(), triangles: b.triangleCount };
}

/**
 * Bakes a placement of a template into a builder. Use this where the renderer only has merged
 * meshes. A renderer with hardware instancing can use the same `Placement` list and the template's
 * vertices directly, so the placement lists double as the instance data.
 */
export function stamp(b: MeshBuilder, t: Template, p: Placement): void {
  const rot = p.rot ?? 0, s = p.scale ?? 1, oy = p.y ?? 0;
  const cs = Math.cos(rot), sn = Math.sin(rot);
  const v = t.vertices;
  const P = (i: number): number[] => {
    const x = v[i], y = v[i + 1], z = v[i + 2];
    return [p.x + s * (x * cs + z * sn), oy + s * y, p.z + s * (-x * sn + z * cs)];
  };
  // Normals turn with the heading (a uniform scale leaves them unit length), so smooth shaded templates stay smooth.
  const N = (i: number): number[] => [v[i + 3] * cs + v[i + 5] * sn, v[i + 4], -v[i + 3] * sn + v[i + 5] * cs];
  const C = (i: number): Rgb => [v[i + 6], v[i + 7], v[i + 8]];
  for (let i = 0; i + 3 * VERTEX_FLOATS <= v.length; i += 3 * VERTEX_FLOATS) {
    const a = i, c1 = i + VERTEX_FLOATS, c2 = i + 2 * VERTEX_FLOATS;
    b.triN(P(a), P(c1), P(c2), N(a), N(c1), N(c2), C(a), C(c1), C(c2), v[a + 9]);
  }
}

/** Deterministic integer pick. */
export function pick<T>(rnd: () => number, list: readonly T[]): T {
  return list[Math.min(list.length - 1, Math.floor(rnd() * list.length))];
}

export const range = (rnd: () => number, lo: number, hi: number): number => lo + (hi - lo) * rnd();

/** A template with the placements that use it, for renderers that draw instances. */
export type InstanceLike = Readonly<{ template: Template; placements: readonly Placement[] }>;
