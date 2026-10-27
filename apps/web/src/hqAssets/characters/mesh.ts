/**
 * A tiny triangle recorder for the character kit. Pure data in, typed arrays out: no three.js and no DOM, so the
 * unit runner measures the triangle counts. Every triangle carries a role (which part of the person it is, so the
 * renderer can tint it per figure), a shade (the painted light and dark of the part) and, for parts that keep their
 * own colour (eyes, shoes, a safety vest), an sRGB colour.
 */
export type V3 = readonly [number, number, number];
/** Which part of a person a vertex is. The numbers are the ones `hqFigureRoles.ts` and the role shader use. */
export const ROLE = { skin: 0, hair: 1, top: 2, bottom: 3, trim: 4, accent: 5, keep: 6 } as const;
export type RoleId = (typeof ROLE)[keyof typeof ROLE];
/** Row major 3x3 rotation plus a translation. */
export type Xf = Readonly<{ r: readonly number[]; t: V3 }>;

export const add = (a: V3, b: V3): V3 => [a[0] + b[0], a[1] + b[1], a[2] + b[2]];
export const sub = (a: V3, b: V3): V3 => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
export const mul = (a: V3, k: number): V3 => [a[0] * k, a[1] * k, a[2] * k];
export const dot = (a: V3, b: V3): number => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
export const cross = (a: V3, b: V3): V3 => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
export const len = (a: V3): number => Math.hypot(a[0], a[1], a[2]);
export const norm = (a: V3): V3 => { const l = len(a) || 1; return [a[0] / l, a[1] / l, a[2] / l]; };
export const lerp = (a: V3, b: V3, k: number): V3 => [a[0] + (b[0] - a[0]) * k, a[1] + (b[1] - a[1]) * k, a[2] + (b[2] - a[2]) * k];

export const IDENTITY: Xf = { r: [1, 0, 0, 0, 1, 0, 0, 0, 1], t: [0, 0, 0] };
const apply = (x: Xf, p: V3): V3 => [
  x.r[0] * p[0] + x.r[1] * p[1] + x.r[2] * p[2] + x.t[0],
  x.r[3] * p[0] + x.r[4] * p[1] + x.r[5] * p[2] + x.t[1],
  x.r[6] * p[0] + x.r[7] * p[1] + x.r[8] * p[2] + x.t[2],
];
const applyDir = (x: Xf, p: V3): V3 => [
  x.r[0] * p[0] + x.r[1] * p[1] + x.r[2] * p[2], x.r[3] * p[0] + x.r[4] * p[1] + x.r[5] * p[2], x.r[6] * p[0] + x.r[7] * p[1] + x.r[8] * p[2],
];

/** Rotation about `pivot`: pitch about x (positive tips the face down), yaw about y (positive turns to +x), roll about z. */
export function rotation(pivot: V3, pitch = 0, yaw = 0, roll = 0): Xf {
  const cx = Math.cos(pitch), sx = Math.sin(pitch), cy = Math.cos(yaw), sy = Math.sin(yaw), cz = Math.cos(roll), sz = Math.sin(roll);
  // R = Ry * Rx * Rz
  const rx = [1, 0, 0, 0, cx, -sx, 0, sx, cx], ry = [cy, 0, sy, 0, 1, 0, -sy, 0, cy], rz = [cz, -sz, 0, sz, cz, 0, 0, 0, 1];
  const m = (a: number[], b: number[]): number[] => [
    a[0] * b[0] + a[1] * b[3] + a[2] * b[6], a[0] * b[1] + a[1] * b[4] + a[2] * b[7], a[0] * b[2] + a[1] * b[5] + a[2] * b[8],
    a[3] * b[0] + a[4] * b[3] + a[5] * b[6], a[3] * b[1] + a[4] * b[4] + a[5] * b[7], a[3] * b[2] + a[4] * b[5] + a[5] * b[8],
    a[6] * b[0] + a[7] * b[3] + a[8] * b[6], a[6] * b[1] + a[7] * b[4] + a[8] * b[7], a[6] * b[2] + a[7] * b[5] + a[8] * b[8],
  ];
  const r = m(m(ry, rx), rz);
  const rp: V3 = [r[0] * pivot[0] + r[1] * pivot[1] + r[2] * pivot[2], r[3] * pivot[0] + r[4] * pivot[1] + r[5] * pivot[2], r[6] * pivot[0] + r[7] * pivot[1] + r[8] * pivot[2]];
  return { r, t: [pivot[0] - rp[0], pivot[1] - rp[1], pivot[2] - rp[2]] };
}

export type Shade = number | readonly [number, number, number];

export class Mesh {
  readonly pos: number[] = [];
  readonly nor: number[] = [];
  readonly col: number[] = [];
  readonly role: number[] = [];
  private xf: Xf = IDENTITY;

  get triangles(): number { return this.pos.length / 9; }

  /** Runs `fn` with every point transformed by `x` (applied after any transform already active). */
  with(x: Xf, fn: () => void): void {
    const before = this.xf;
    this.xf = { r: matMul(before.r, x.r), t: apply(before, x.t) };
    fn();
    this.xf = before;
  }

  /** One triangle with its own vertex normals. Degenerate triangles are dropped. */
  tri(a: V3, b: V3, c: V3, na: V3, nb: V3, nc: V3, role: RoleId, shade: Shade): void {
    const pa = apply(this.xf, a), pb = apply(this.xf, b), pc = apply(this.xf, c);
    if (len(cross(sub(pb, pa), sub(pc, pa))) < 1e-9) return;
    const col = typeof shade === 'number' ? [shade, shade, shade] : shade;
    // Wind the triangle counter clockwise seen from the side its normals point to (a renderer that draws both sides lights by the winding).
    const wind = dot(cross(sub(pb, pa), sub(pc, pa)), add(add(applyDir(this.xf, na), applyDir(this.xf, nb)), applyDir(this.xf, nc))) >= 0;
    const order = wind ? ([[pa, na], [pb, nb], [pc, nc]] as const) : ([[pa, na], [pc, nc], [pb, nb]] as const);
    for (const [p, n] of order) {
      const q = norm(applyDir(this.xf, n));
      this.pos.push(p[0], p[1], p[2]); this.nor.push(q[0], q[1], q[2]); this.col.push(col[0], col[1], col[2]); this.role.push(role);
    }
  }

  /** A flat triangle, the normal from the winding. */
  flat(a: V3, b: V3, c: V3, role: RoleId, shade: Shade): void {
    const n = norm(cross(sub(b, a), sub(c, a)));
    this.tri(a, b, c, n, n, n, role, shade);
  }
}

const matMul = (a: readonly number[], b: readonly number[]): number[] => [
  a[0] * b[0] + a[1] * b[3] + a[2] * b[6], a[0] * b[1] + a[1] * b[4] + a[2] * b[7], a[0] * b[2] + a[1] * b[5] + a[2] * b[8],
  a[3] * b[0] + a[4] * b[3] + a[5] * b[6], a[3] * b[1] + a[4] * b[4] + a[5] * b[7], a[3] * b[2] + a[4] * b[5] + a[5] * b[8],
  a[6] * b[0] + a[7] * b[3] + a[8] * b[6], a[6] * b[1] + a[7] * b[4] + a[8] * b[7], a[6] * b[2] + a[7] * b[5] + a[8] * b[8],
];

/** An elliptical ring: `c` the centre, `u` and `v` the two semi axes as vectors. */
export type Ring = Readonly<{ c: V3; u: V3; v: V3 }>;

/** A ring around `axis` with semi axes `rx` (towards `side`, made square to the axis) and `rz`. */
export function ringOf(c: V3, axis: V3, rx: number, rz: number, side: V3 = [1, 0, 0]): Ring {
  const a = norm(axis);
  let s = sub(side, mul(a, dot(side, a)));
  if (len(s) < 1e-4) s = sub([0, 0, 1], mul(a, a[2]));
  const u = norm(s);
  const v = norm(cross(a, u));
  return { c, u: mul(u, rx), v: mul(v, rz) };
}

export type LoftOptions = Readonly<{
  /** Close the first and last ring with a flat cap. */
  capStart?: boolean; capEnd?: boolean;
  /** Flat shading per quad (a sharp, cut look) instead of smooth normals. */
  flat?: boolean;
  /** Only the part of the way round from `a0` to `a1` (radians), for a scarf that leaves the face open. */
  arc?: readonly [number, number];
  /** A brightness change along the loft, first ring to last ring (1 means none). */
  fade?: readonly [number, number];
}>;

/**
 * A surface through a list of rings with `seg` points round each (a limb, a torso, a skirt, a dome). Smooth normals are
 * computed from the faces and pointed away from the axis, so a part can be drawn whichever way its rings run.
 */
export function loft(m: Mesh, rings: readonly Ring[], seg: number, role: RoleId, shade: Shade, o: LoftOptions = {}): void {
  const n = rings.length;
  if (n < 2) return;
  const a0 = o.arc?.[0] ?? 0, a1 = o.arc?.[1] ?? Math.PI * 2;
  const full = !o.arc;
  const cols = full ? seg : seg + 1;
  const grid: V3[][] = rings.map((r) => {
    const row: V3[] = [];
    for (let k = 0; k < cols; k++) {
      const t = a0 + ((a1 - a0) * k) / seg;
      row.push(add(r.c, add(mul(r.u, Math.cos(t)), mul(r.v, Math.sin(t)))));
    }
    return row;
  });
  const at = (i: number, k: number): V3 => grid[i][full ? k % seg : k];
  const normals: V3[][] = grid.map((row) => row.map(() => [0, 0, 0] as V3));
  const quads = full ? seg : seg;
  for (let i = 0; i < n - 1; i++) {
    for (let k = 0; k < quads; k++) {
      const p00 = at(i, k), p01 = at(i, k + 1), p10 = at(i + 1, k), p11 = at(i + 1, k + 1);
      for (const [a, b, c] of [[p00, p01, p11], [p00, p11, p10]] as const) {
        const fn = cross(sub(b, a), sub(c, a));
        for (const [ii, kk] of [[i, k], [i, k + 1], [i + 1, k + 1], [i + 1, k]] as const) {
          const kc = full ? kk % seg : kk;
          normals[ii][kc] = add(normals[ii][kc], fn);
        }
      }
    }
  }
  // Point the normals away from the loft: one decision for the whole surface (from the rings with a real radius), so a pole or a
  // thin ring can never flip on its own.
  let side = 0;
  for (let i = 0; i < n; i++) {
    const axis = norm(sub(rings[Math.min(i + 1, n - 1)].c, rings[Math.max(i - 1, 0)].c));
    for (let k = 0; k < cols; k++) {
      normals[i][k] = norm(normals[i][k]);
      const radial = sub(grid[i][k], rings[i].c);
      side += dot(normals[i][k], sub(radial, mul(axis, dot(radial, axis))));
    }
  }
  if (side < 0) for (let i = 0; i < n; i++) for (let k = 0; k < cols; k++) normals[i][k] = mul(normals[i][k], -1);
  // A pole (a ring with no size) has no radial direction: point it along the loft's own axis, outward.
  const isPole = (r: Ring): boolean => len(r.u) < 1e-6 && len(r.v) < 1e-6;
  if (isPole(rings[0])) { const nn = norm(sub(rings[0].c, rings[1].c)); for (let k = 0; k < cols; k++) normals[0][k] = nn; }
  if (isPole(rings[n - 1])) { const nn = norm(sub(rings[n - 1].c, rings[n - 2].c)); for (let k = 0; k < cols; k++) normals[n - 1][k] = nn; }
  const shadeAt = (i: number): Shade => {
    if (!o.fade || typeof shade !== 'number') return shade;
    return shade * (o.fade[0] + (o.fade[1] - o.fade[0]) * (i / (n - 1)));
  };
  for (let i = 0; i < n - 1; i++) {
    for (let k = 0; k < quads; k++) {
      const kc = (j: number): number => (full ? j % seg : j);
      const p00 = at(i, k), p01 = at(i, k + 1), p10 = at(i + 1, k), p11 = at(i + 1, k + 1);
      const n00 = normals[i][kc(k)], n01 = normals[i][kc(k + 1)], n10 = normals[i + 1][kc(k)], n11 = normals[i + 1][kc(k + 1)];
      const s0 = shadeAt(i), s1 = shadeAt(i + 1);
      if (o.flat) {
        const qn = norm(add(add(n00, n01), add(n10, n11)));
        m.tri(p00, p01, p11, qn, qn, qn, role, s0); m.tri(p00, p11, p10, qn, qn, qn, role, s1);
      } else {
        m.tri(p00, p01, p11, n00, n01, n11, role, s0); m.tri(p00, p11, p10, n00, n11, n10, role, s1);
      }
    }
  }
  const cap = (i: number, sign: number): void => {
    if (!full) return;
    const r = rings[i];
    const axis = norm(cross(r.u, r.v));
    const nn = mul(axis, sign * (i === 0 ? -1 : 1) * (dot(axis, sub(rings[n - 1].c, rings[0].c)) >= 0 ? 1 : -1));
    for (let k = 0; k < seg; k++) m.tri(r.c, at(i, k), at(i, k + 1), nn, nn, nn, role, shade);
  };
  if (o.capStart) cap(0, 1);
  if (o.capEnd) cap(n - 1, -1);
}

/** An axis aligned ellipsoid (or a dome of one: `from` and `to` are the latitude range, 0 the bottom pole, 1 the top). */
export function ellipsoid(m: Mesh, c: V3, rx: number, ry: number, rz: number, seg: number, stacks: number, role: RoleId, shade: Shade, o: { from?: number; to?: number; arc?: readonly [number, number]; flat?: boolean } = {}): void {
  const from = o.from ?? 0, to = o.to ?? 1;
  const rings: Ring[] = [];
  for (let i = 0; i <= stacks; i++) {
    const f = from + ((to - from) * i) / stacks;
    const lat = Math.PI * (1 - f);
    const s = Math.sin(lat), y = Math.cos(lat);
    rings.push({ c: [c[0], c[1] + ry * y, c[2]], u: [rx * s, 0, 0], v: [0, 0, rz * s] });
  }
  loft(m, rings, seg, role, shade, { arc: o.arc, flat: o.flat });
}

/** A parallelepiped from a centre and three half vectors, flat shaded. */
export function obox(m: Mesh, c: V3, hx: V3, hy: V3, hz: V3, role: RoleId, shade: Shade): void {
  const p = (sx: number, sy: number, sz: number): V3 => add(c, add(add(mul(hx, sx), mul(hy, sy)), mul(hz, sz)));
  const face = (a: V3, b: V3, cc: V3, d: V3, outward: V3): void => {
    // The camera never looks up from below: a face that points straight down is not drawn.
    if (norm(outward)[1] < -0.9) return;
    const n = cross(sub(b, a), sub(cc, a));
    if (dot(n, outward) >= 0) { m.flat(a, b, cc, role, shade); m.flat(a, cc, d, role, shade); } else { m.flat(a, cc, b, role, shade); m.flat(a, d, cc, role, shade); }
  };
  face(p(-1, -1, 1), p(1, -1, 1), p(1, 1, 1), p(-1, 1, 1), hz);
  face(p(-1, -1, -1), p(1, -1, -1), p(1, 1, -1), p(-1, 1, -1), mul(hz, -1));
  face(p(1, -1, -1), p(1, -1, 1), p(1, 1, 1), p(1, 1, -1), hx);
  face(p(-1, -1, -1), p(-1, -1, 1), p(-1, 1, 1), p(-1, 1, -1), mul(hx, -1));
  face(p(-1, 1, -1), p(1, 1, -1), p(1, 1, 1), p(-1, 1, 1), hy);
  face(p(-1, -1, -1), p(1, -1, -1), p(1, -1, 1), p(-1, -1, 1), mul(hy, -1));
}

/** An axis aligned box from a centre and full sizes. */
export const abox = (m: Mesh, c: V3, w: number, h: number, d: number, role: RoleId, shade: Shade): void => obox(m, c, [w / 2, 0, 0], [0, h / 2, 0], [0, 0, d / 2], role, shade);

/** A box along the line from `a` to `b`, `w` wide and `d` deep (a strap, a frame arm, a cuff). */
export function strip(m: Mesh, a: V3, b: V3, w: number, d: number, role: RoleId, shade: Shade, side: V3 = [1, 0, 0]): void {
  const dir = sub(b, a);
  const axis = norm(dir);
  let s = sub(side, mul(axis, dot(side, axis)));
  if (len(s) < 1e-4) s = [0, 0, 1];
  const u = norm(s), v = norm(cross(axis, u));
  obox(m, lerp(a, b, 0.5), mul(u, w / 2), mul(dir, 0.5), mul(v, d / 2), role, shade);
}

/** The typed arrays a renderer needs. */
export type MeshData = Readonly<{ positions: Float32Array; normals: Float32Array; colors: Float32Array; roles: Float32Array; triangles: number }>;
export function toData(m: Mesh): MeshData {
  return { positions: new Float32Array(m.pos), normals: new Float32Array(m.nor), colors: new Float32Array(m.col), roles: new Float32Array(m.role), triangles: m.triangles };
}
