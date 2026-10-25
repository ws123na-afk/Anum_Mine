/**
 * A local frame for building furniture: an origin on the floor plan, a quarter turn (0..3) and a
 * base height. Local +z is the "front" of the piece. Boxes use the shared `lbox`; prisms, blobs and
 * free triangles are rotated here so a whole piece can be turned with one number.
 */
import { MeshBuilder, scale, type Rgb } from '../../lib/hqGeometry.ts';
import { lbox } from '../../lib/hqProps.ts';

export type BoxOpts = { emissive?: number; shade?: boolean; grad?: number; skip?: readonly ('top' | 'bottom' | 'north' | 'south' | 'west' | 'east')[] };
export type P3 = readonly [number, number, number];

export class Local {
  readonly b: MeshBuilder;
  readonly ox: number;
  readonly oz: number;
  readonly rot: number;
  readonly y0: number;

  constructor(b: MeshBuilder, ox: number, oz: number, rot = 0, y0 = 0) {
    this.b = b;
    this.ox = ox;
    this.oz = oz;
    this.rot = rot;
    this.y0 = y0;
  }

  /** World x and z of a local point. */
  pt(lx: number, lz: number): [number, number] {
    const r = ((this.rot % 4) + 4) % 4;
    if (r === 0) return [this.ox + lx, this.oz + lz];
    if (r === 1) return [this.ox - lz, this.oz + lx];
    if (r === 2) return [this.ox - lx, this.oz - lz];
    return [this.ox + lz, this.oz - lx];
  }

  w(p: P3): [number, number, number] {
    const [x, z] = this.pt(p[0], p[2]);
    return [x, this.y0 + p[1], z];
  }

  /** A child frame at a local point, turned by `rot2` more quarter turns, `dy` higher. */
  sub(lx: number, lz: number, rot2 = 0, dy = 0): Local {
    const [x, z] = this.pt(lx, lz);
    return new Local(this.b, x, z, this.rot + rot2, this.y0 + dy);
  }

  box(lx: number, ly: number, lz: number, w: number, h: number, d: number, color: Rgb, o: BoxOpts = {}): void {
    // The camera never looks up from below, so undersides are never drawn.
    const skip = o.skip && o.skip.includes('bottom') ? o.skip : [...(o.skip ?? []), 'bottom' as const];
    lbox(this.b, this.ox, this.oz, this.rot, lx, this.y0 + ly, lz, w, h, d, color, { ...o, skip });
  }

  prism(lx: number, ly: number, lz: number, rb: number, rt: number, h: number, sides: number, color: Rgb, o: { emissive?: number; capTop?: boolean } = {}): void {
    const [x, z] = this.pt(lx, lz);
    this.b.prism(x, this.y0 + ly, z, rb, rt, h, sides, color, o);
  }

  blob(lx: number, ly: number, lz: number, rx: number, ry: number, rz: number, color: Rgb, seg = 6): void {
    const [x, z] = this.pt(lx, lz);
    const odd = ((this.rot % 2) + 2) % 2 === 1;
    this.b.blob(x, this.y0 + ly, z, odd ? rz : rx, ry, odd ? rx : rz, color, seg);
  }

  quad(a: P3, b: P3, c: P3, d: P3, color: Rgb, emissive = 0): void {
    this.b.quad(this.w(a), this.w(b), this.w(c), this.w(d), color, emissive);
  }

  tri(a: P3, b: P3, c: P3, color: Rgb, emissive = 0): void {
    this.b.tri(this.w(a), this.w(b), this.w(c), color, emissive);
  }

  /**
   * A four sided tapered limb between two local points (arms, legs, torsos, lamp arms). `w0`/`d0`
   * are the full width and depth at `p0`, `w1`/`d1` at `p1`. The far end is closed when `cap` is set.
   */
  limb(p0: P3, p1: P3, w0: number, d0: number, w1: number, d1: number, color: Rgb, o: { helper?: P3; cap?: boolean; emissive?: number } = {}): void {
    const dx = p1[0] - p0[0], dy = p1[1] - p0[1], dz = p1[2] - p0[2];
    const len = Math.hypot(dx, dy, dz);
    if (len < 1e-6) return;
    const d: P3 = [dx / len, dy / len, dz / len];
    let h: P3 = o.helper ?? [0, 1, 0];
    const dot = Math.abs(d[0] * h[0] + d[1] * h[1] + d[2] * h[2]);
    if (dot > 0.92) h = Math.abs(d[1]) > 0.9 ? [0, 0, 1] : [1, 0, 0];
    const cross = (a: P3, c: P3): P3 => [a[1] * c[2] - a[2] * c[1], a[2] * c[0] - a[0] * c[2], a[0] * c[1] - a[1] * c[0]];
    const norm = (a: P3): P3 => { const l = Math.hypot(a[0], a[1], a[2]) || 1; return [a[0] / l, a[1] / l, a[2] / l]; };
    const u = norm(cross(d, h));
    const v = norm(cross(d, u));
    const corners = [[-1, -1], [1, -1], [1, 1], [-1, 1]] as const;
    const ring = (p: P3, w: number, dd: number): P3[] => corners.map(([su, sv]) => [
      p[0] + u[0] * su * w / 2 + v[0] * sv * dd / 2,
      p[1] + u[1] * su * w / 2 + v[1] * sv * dd / 2,
      p[2] + u[2] * su * w / 2 + v[2] * sv * dd / 2,
    ] as P3);
    const r0 = ring(p0, w0, d0), r1 = ring(p1, w1, d1);
    const mid: P3 = [(p0[0] + p1[0]) / 2, (p0[1] + p1[1]) / 2, (p0[2] + p1[2]) / 2];
    const em = o.emissive ?? 0;
    for (let i = 0; i < 4; i++) {
      const j = (i + 1) % 4;
      const a = r0[i], b = r0[j], c = r1[j], e = r1[i];
      const fc: P3 = [(a[0] + b[0] + c[0] + e[0]) / 4, (a[1] + b[1] + c[1] + e[1]) / 4, (a[2] + b[2] + c[2] + e[2]) / 4];
      const e1: P3 = [b[0] - a[0], b[1] - a[1], b[2] - a[2]], e2: P3 = [e[0] - a[0], e[1] - a[1], e[2] - a[2]];
      const n = cross(e1, e2);
      const out = n[0] * (fc[0] - mid[0]) + n[1] * (fc[1] - mid[1]) + n[2] * (fc[2] - mid[2]);
      const shade = scale(color, 0.72 + 0.28 * Math.max(0, norm(n)[1] * (out >= 0 ? 1 : -1) + 0.4));
      if (out >= 0) this.quad(a, b, c, e, shade, em); else this.quad(a, e, c, b, shade, em);
    }
    if (o.cap) {
      const n = cross([r1[1][0] - r1[0][0], r1[1][1] - r1[0][1], r1[1][2] - r1[0][2]], [r1[3][0] - r1[0][0], r1[3][1] - r1[0][1], r1[3][2] - r1[0][2]]);
      const facing = n[0] * d[0] + n[1] * d[1] + n[2] * d[2] >= 0;
      if (facing) this.quad(r1[0], r1[1], r1[2], r1[3], scale(color, 1.05), em); else this.quad(r1[0], r1[3], r1[2], r1[1], scale(color, 1.05), em);
    }
  }
}

/** A soft darker patch under furniture so it sits on the floor. */
export function contact(b: MeshBuilder, x: number, z: number, w: number, d: number, y: number, floor: Rgb): void {
  b.plane(x - 0.1, y + 0.016, z - 0.08, w + 0.2, d + 0.2, scale(floor, 0.62), 0.28);
}
