/**
 * 2D path helpers (x east, z south) and ribbon meshes: the base of ring roads, walkways, canals
 * and fences. A ribbon is a strip between two offsets of a path, each at its own height, so one
 * helper draws flat paint, raised kerbs and vertical kerb faces.
 */
import { MeshBuilder, type Rgb } from '../../lib/hqGeometry.ts';

export type P2 = readonly [number, number];

/** Rounded rectangle, clockwise on screen (x east, z south), corner arcs of `segs` steps. */
export function roundedRectPath(cx: number, cz: number, halfW: number, halfD: number, radius: number, segs = 5): P2[] {
  const r = Math.max(0, Math.min(radius, halfW, halfD));
  const pts: P2[] = [];
  const corners: Array<[number, number, number]> = [
    [cx + halfW - r, cz - halfD + r, -Math.PI / 2],
    [cx + halfW - r, cz + halfD - r, 0],
    [cx - halfW + r, cz + halfD - r, Math.PI / 2],
    [cx - halfW + r, cz - halfD + r, Math.PI],
  ];
  for (const [px, pz, a0] of corners) {
    if (r === 0) { pts.push([px, pz]); continue; }
    for (let i = 0; i <= segs; i++) {
      const a = a0 + (i / segs) * (Math.PI / 2);
      pts.push([px + Math.cos(a) * r, pz + Math.sin(a) * r]);
    }
  }
  return pts;
}

/** Cumulative arc length of each point, and the total (closed paths include the last edge). */
export function lengths(pts: readonly P2[], closed: boolean): { at: number[]; total: number } {
  const at = [0];
  const n = closed ? pts.length : pts.length - 1;
  for (let i = 0; i < n; i++) {
    const a = pts[i], b = pts[(i + 1) % pts.length];
    at.push(at[i] + Math.hypot(b[0] - a[0], b[1] - a[1]));
  }
  return { at, total: at[at.length - 1] };
}

/** Points of the path between arc lengths s0 and s1 (interpolated ends). */
export function subPath(pts: readonly P2[], closed: boolean, s0: number, s1: number): P2[] {
  const { at, total } = lengths(pts, closed);
  const out: P2[] = [];
  const n = closed ? pts.length : pts.length - 1;
  const lerp = (i: number, s: number): P2 => {
    const a = pts[i], b = pts[(i + 1) % pts.length];
    const t = at[i + 1] === at[i] ? 0 : (s - at[i]) / (at[i + 1] - at[i]);
    return [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t];
  };
  const lo = Math.max(0, s0), hi = Math.min(total, s1);
  if (hi <= lo) return out;
  for (let i = 0; i < n; i++) {
    if (at[i + 1] <= lo || at[i] >= hi) continue;
    if (out.length === 0) out.push(lerp(i, Math.max(lo, at[i])));
    if (at[i + 1] < hi) out.push(pts[(i + 1) % pts.length]);
    else out.push(lerp(i, hi));
  }
  return out;
}

/** Unit direction of the path at distance s. */
export function directionAt(pts: readonly P2[], closed: boolean, s: number): { p: P2; dx: number; dz: number } {
  const { at, total } = lengths(pts, closed);
  const ss = closed ? ((s % total) + total) % total : Math.max(0, Math.min(total, s));
  const n = closed ? pts.length : pts.length - 1;
  for (let i = 0; i < n; i++) {
    if (ss <= at[i + 1] || i === n - 1) {
      const a = pts[i], b = pts[(i + 1) % pts.length];
      const l = Math.hypot(b[0] - a[0], b[1] - a[1]) || 1;
      const t = (ss - at[i]) / l;
      return { p: [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t], dx: (b[0] - a[0]) / l, dz: (b[1] - a[1]) / l };
    }
  }
  return { p: pts[0], dx: 1, dz: 0 };
}

/** Offset (mitred) points: positive offset is to the path's right-hand normal (-dz, dx). */
export function offsetPath(pts: readonly P2[], closed: boolean, offset: number): P2[] {
  const n = pts.length;
  const norm = (a: P2, b: P2): P2 => {
    const l = Math.hypot(b[0] - a[0], b[1] - a[1]) || 1;
    return [-(b[1] - a[1]) / l, (b[0] - a[0]) / l];
  };
  return pts.map((p, i) => {
    const prev = closed ? pts[(i + n - 1) % n] : pts[Math.max(0, i - 1)];
    const next = closed ? pts[(i + 1) % n] : pts[Math.min(n - 1, i + 1)];
    const n1 = i === 0 && !closed ? norm(p, next) : norm(prev, p);
    const n2 = i === n - 1 && !closed ? norm(prev, p) : norm(p, next);
    let mx = n1[0] + n2[0], mz = n1[1] + n2[1];
    const ml = Math.hypot(mx, mz) || 1;
    mx /= ml; mz /= ml;
    const k = Math.min(2, 1 / Math.max(0.5, mx * n2[0] + mz * n2[1]));
    return [p[0] + mx * offset * k, p[1] + mz * offset * k] as P2;
  });
}

/**
 * Strip between offset o0 at height y0 and offset o1 at height y1. `face` is the direction the
 * strip must face: 'up', or 'right' / 'left' for vertical kerb faces.
 */
export function ribbon(
  b: MeshBuilder, pts: readonly P2[], closed: boolean, o0: number, y0: number, o1: number, y1: number, color: Rgb,
  face: 'up' | 'right' | 'left' = 'up', emissive = 0,
): void {
  if (pts.length < 2) return;
  const a = offsetPath(pts, closed, o0), c = offsetPath(pts, closed, o1);
  const n = closed ? pts.length : pts.length - 1;
  for (let i = 0; i < n; i++) {
    const j = (i + 1) % pts.length;
    const q: number[][] = [[a[i][0], y0, a[i][1]], [a[j][0], y0, a[j][1]], [c[j][0], y1, c[j][1]], [c[i][0], y1, c[i][1]]];
    const ux = q[1][0] - q[0][0], uy = q[1][1] - q[0][1], uz = q[1][2] - q[0][2];
    const vx = q[3][0] - q[0][0], vy = q[3][1] - q[0][1], vz = q[3][2] - q[0][2];
    const nx = uy * vz - uz * vy, ny = uz * vx - ux * vz, nz = ux * vy - uy * vx;
    const dx = pts[j][0] - pts[i][0], dz = pts[j][1] - pts[i][1];
    const want: [number, number, number] = face === 'up' ? [0, 1, 0] : face === 'right' ? [-dz, 0, dx] : [dz, 0, -dx];
    const flip = nx * want[0] + ny * want[1] + nz * want[2] < 0;
    if (flip) b.quad(q[0], q[3], q[2], q[1], color, emissive);
    else b.quad(q[0], q[1], q[2], q[3], color, emissive);
  }
}

/** Dashes along a path: `dash` metres on, `gap` metres off, painted as flat strips. */
export function dashedRibbon(
  b: MeshBuilder, pts: readonly P2[], closed: boolean, offset: number, halfWidth: number, y: number, dash: number, gap: number, color: Rgb, start = 0,
): void {
  const { total } = lengths(pts, closed);
  for (let s = start; s < total; s += dash + gap) {
    const seg = subPath(pts, closed, s, Math.min(total, s + dash));
    if (seg.length >= 2) ribbon(b, seg, false, offset - halfWidth, y, offset + halfWidth, y, color);
  }
}

/** Perpendicular marks across a path every `step` metres (tile joints, sleepers, crosswalk bars). */
export function crossMarks(
  b: MeshBuilder, pts: readonly P2[], closed: boolean, o0: number, o1: number, y: number, step: number, thickness: number, color: Rgb, start = 0,
): void {
  const { total } = lengths(pts, closed);
  for (let s = start; s < total; s += step) {
    const d = directionAt(pts, closed, s);
    const nx = -d.dz, nz = d.dx;
    const hx = d.dx * thickness * 0.5, hz = d.dz * thickness * 0.5;
    const A = [d.p[0] + nx * o0 - hx, y, d.p[1] + nz * o0 - hz];
    const B = [d.p[0] + nx * o1 - hx, y, d.p[1] + nz * o1 - hz];
    const C = [d.p[0] + nx * o1 + hx, y, d.p[1] + nz * o1 + hz];
    const D = [d.p[0] + nx * o0 + hx, y, d.p[1] + nz * o0 + hz];
    const ux = B[0] - A[0], uz = B[2] - A[2], vx = D[0] - A[0], vz = D[2] - A[2];
    const ny = uz * vx - ux * vz;
    if (ny >= 0) b.quad(A, B, C, D, color); else b.quad(A, D, C, B, color);
  }
}

/** Ring marks: a flat circle outline of `radius` and `half` width, as a closed ribbon. */
export function circlePath(cx: number, cz: number, r: number, segs = 20): P2[] {
  const pts: P2[] = [];
  for (let i = 0; i < segs; i++) {
    const a = (i / segs) * Math.PI * 2;
    pts.push([cx + Math.cos(a) * r, cz + Math.sin(a) * r]);
  }
  return pts;
}
