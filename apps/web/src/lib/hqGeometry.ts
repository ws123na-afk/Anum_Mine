/**
 * Low-poly mesh builder for the headquarters scenes.
 *
 * A MeshBuilder collects triangles with flat normals, a base colour and an `emissive` weight
 * (0 for plain surfaces, up to 1 for lit windows and screens). The renderer multiplies the
 * emissive weight by the night factor, so day and night share one vertex buffer.
 * Pure data in, typed arrays out: no WebGL here, so the unit runner covers it.
 */
import type { Aabb } from './hqMath.ts';

export type Rgb = readonly [number, number, number];
/** Floats per vertex: position 3, normal 3, colour 3, emissive 1. */
export const VERTEX_FLOATS = 10;

export function hex(value: string): Rgb {
  const m = /^#([0-9a-f]{6})$/i.exec(value);
  if (!m) return [0.5, 0.5, 0.5];
  const n = parseInt(m[1], 16);
  return [((n >> 16) & 255) / 255, ((n >> 8) & 255) / 255, (n & 255) / 255];
}

export function mix(a: Rgb, b: Rgb, w: number): Rgb {
  return [a[0] + (b[0] - a[0]) * w, a[1] + (b[1] - a[1]) * w, a[2] + (b[2] - a[2]) * w];
}

export function scale(c: Rgb, f: number): Rgb {
  return [Math.min(1, c[0] * f), Math.min(1, c[1] * f), Math.min(1, c[2] * f)];
}

export class MeshBuilder {
  private data: number[] = [];
  private lo = [Infinity, Infinity, Infinity];
  private hi = [-Infinity, -Infinity, -Infinity];

  get vertexCount(): number {
    return this.data.length / VERTEX_FLOATS;
  }

  get triangleCount(): number {
    return this.vertexCount / 3;
  }

  bounds(): Aabb {
    if (this.vertexCount === 0) return { min: { x: 0, y: 0, z: 0 }, max: { x: 0, y: 0, z: 0 } };
    return {
      min: { x: this.lo[0], y: this.lo[1], z: this.lo[2] },
      max: { x: this.hi[0], y: this.hi[1], z: this.hi[2] },
    };
  }

  tri(
    a: readonly number[], b: readonly number[], c: readonly number[], color: Rgb, emissive = 0,
  ): void {
    const ux = b[0] - a[0], uy = b[1] - a[1], uz = b[2] - a[2];
    const vx = c[0] - a[0], vy = c[1] - a[1], vz = c[2] - a[2];
    let nx = uy * vz - uz * vy, ny = uz * vx - ux * vz, nz = ux * vy - uy * vx;
    const len = Math.hypot(nx, ny, nz);
    if (len < 1e-12) return;
    nx /= len; ny /= len; nz /= len;
    for (const p of [a, b, c]) {
      this.data.push(p[0], p[1], p[2], nx, ny, nz, color[0], color[1], color[2], emissive);
      for (let i = 0; i < 3; i++) {
        if (p[i] < this.lo[i]) this.lo[i] = p[i];
        if (p[i] > this.hi[i]) this.hi[i] = p[i];
      }
    }
  }

  /** Triangle with a colour per vertex (soft vertical gradients on facades), flat normal. */
  triV(
    a: readonly number[], b: readonly number[], c: readonly number[], ca: Rgb, cb: Rgb, cc: Rgb, emissive = 0,
  ): void {
    const ux = b[0] - a[0], uy = b[1] - a[1], uz = b[2] - a[2];
    const vx = c[0] - a[0], vy = c[1] - a[1], vz = c[2] - a[2];
    let nx = uy * vz - uz * vy, ny = uz * vx - ux * vz, nz = ux * vy - uy * vx;
    const len = Math.hypot(nx, ny, nz);
    if (len < 1e-12) return;
    nx /= len; ny /= len; nz /= len;
    const pts = [a, b, c], cols = [ca, cb, cc];
    for (let k = 0; k < 3; k++) {
      const p = pts[k], col = cols[k];
      this.data.push(p[0], p[1], p[2], nx, ny, nz, col[0], col[1], col[2], emissive);
      for (let i = 0; i < 3; i++) {
        if (p[i] < this.lo[i]) this.lo[i] = p[i];
        if (p[i] > this.hi[i]) this.hi[i] = p[i];
      }
    }
  }

  /** Quad with a colour per corner (a, b, c, d in the same order as `quad`). */
  quadV(
    a: readonly number[], b: readonly number[], c: readonly number[], d: readonly number[],
    ca: Rgb, cb: Rgb, cc: Rgb, cd: Rgb, emissive = 0,
  ): void {
    this.triV(a, b, c, ca, cb, cc, emissive);
    this.triV(a, c, d, ca, cc, cd, emissive);
  }

  /** Counter-clockwise quad seen from the side the normal points to. */
  quad(
    a: readonly number[], b: readonly number[], c: readonly number[], d: readonly number[], color: Rgb, emissive = 0,
  ): void {
    this.tri(a, b, c, color, emissive);
    this.tri(a, c, d, color, emissive);
  }

  /** Axis-aligned box from its minimum corner. `skip` leaves faces out (top, bottom, north, south, west, east). */
  box(
    x: number, y: number, z: number, w: number, h: number, d: number, color: Rgb,
    options: { emissive?: number; skip?: readonly BoxFace[]; shade?: boolean; grad?: number } = {},
  ): void {
    const em = options.emissive ?? 0;
    const grad = options.grad ?? 0;
    const skip = new Set(options.skip ?? []);
    const shade = options.shade !== false;
    const x1 = x + w, y1 = y + h, z1 = z + d;
    const c = (f: number) => (shade ? scale(color, f) : color);
    if (!skip.has('top')) this.quad([x, y1, z], [x, y1, z1], [x1, y1, z1], [x1, y1, z], c(1), em);
    if (!skip.has('bottom')) this.quad([x, y, z], [x1, y, z], [x1, y, z1], [x, y, z1], c(0.6), em);
    const side = (a: number[], b: number[], cc: number[], d: number[], f: number) => {
      if (grad > 0) { const top = c(f), bot = scale(top, 1 - grad); this.quadV(a, b, cc, d, bot, top, top, bot, em); }
      else this.quad(a, b, cc, d, c(f), em);
    };
    if (!skip.has('north')) side([x, y, z], [x, y1, z], [x1, y1, z], [x1, y, z], 0.82);
    if (!skip.has('south')) side([x1, y, z1], [x1, y1, z1], [x, y1, z1], [x, y, z1], 0.78);
    if (!skip.has('west')) side([x, y, z1], [x, y1, z1], [x, y1, z], [x, y, z], 0.7);
    if (!skip.has('east')) side([x1, y, z], [x1, y1, z], [x1, y1, z1], [x1, y, z1], 0.9);
  }

  /** Upright prism with `sides` faces; radiusTop 0 makes a cone. */
  prism(
    cx: number, y: number, cz: number, radiusBottom: number, radiusTop: number, height: number, sides: number,
    color: Rgb, options: { emissive?: number; capTop?: boolean } = {},
  ): void {
    const n = Math.max(3, Math.floor(sides));
    const em = options.emissive ?? 0;
    const ring = (r: number, yy: number, i: number): number[] => {
      const a = (i / n) * Math.PI * 2;
      return [cx + Math.cos(a) * r, yy, cz + Math.sin(a) * r];
    };
    for (let i = 0; i < n; i++) {
      const j = i + 1;
      const shade = 0.72 + 0.28 * Math.cos(((i + 0.5) / n) * Math.PI * 2 - 0.9);
      const b0 = ring(radiusBottom, y, i), b1 = ring(radiusBottom, y, j);
      const t0 = ring(radiusTop, y + height, i), t1 = ring(radiusTop, y + height, j);
      if (radiusTop <= 1e-6) this.tri(b1, b0, t0, scale(color, shade), em);
      else this.quad(b1, b0, t0, t1, scale(color, shade), em);
      if (options.capTop !== false && radiusTop > 1e-6) this.tri([cx, y + height, cz], t1, t0, scale(color, 1.05), em);
    }
  }

  /** Low-poly sphere (two latitude bands plus poles). */
  blob(cx: number, cy: number, cz: number, rx: number, ry: number, rz: number, color: Rgb, segments = 8): void {
    const lat = [-Math.PI / 2, -Math.PI / 5, Math.PI / 5, Math.PI / 2];
    for (let s = 0; s < segments; s++) {
      const a0 = (s / segments) * Math.PI * 2, a1 = ((s + 1) / segments) * Math.PI * 2;
      const shade = 0.75 + 0.25 * Math.cos(((s + 0.5) / segments) * Math.PI * 2 - 0.9);
      const pt = (la: number, a: number) => [cx + Math.cos(la) * Math.cos(a) * rx, cy + Math.sin(la) * ry, cz + Math.cos(la) * Math.sin(a) * rz];
      for (let l = 0; l < lat.length - 1; l++) {
        const p00 = pt(lat[l], a0), p01 = pt(lat[l], a1), p10 = pt(lat[l + 1], a0), p11 = pt(lat[l + 1], a1);
        const lift = 0.85 + 0.15 * (l / (lat.length - 2));
        if (l === 0) this.tri(p01, p00, p10, scale(color, shade * lift));
        else if (l === lat.length - 2) this.tri(p00, p01, p11, scale(color, shade * lift));
        else this.quad(p01, p00, p10, p11, scale(color, shade * lift));
      }
    }
  }

  /** Flat horizontal slab, top face only, for ground, paths and water. */
  plane(x: number, y: number, z: number, w: number, d: number, color: Rgb, emissive = 0): void {
    this.quad([x, y, z], [x, y, z + d], [x + w, y, z + d], [x + w, y, z], color, emissive);
  }

  /** Flat disc (top face), for the plaza and the pond. */
  disc(cx: number, y: number, cz: number, radius: number, sides: number, color: Rgb, emissive = 0): void {
    const n = Math.max(3, Math.floor(sides));
    for (let i = 0; i < n; i++) {
      const a0 = (i / n) * Math.PI * 2, a1 = ((i + 1) / n) * Math.PI * 2;
      this.tri([cx, y, cz], [cx + Math.cos(a1) * radius, y, cz + Math.sin(a1) * radius], [cx + Math.cos(a0) * radius, y, cz + Math.sin(a0) * radius], color, emissive);
    }
  }

  toFloat32(): Float32Array {
    return new Float32Array(this.data);
  }
}

export type BoxFace = 'top' | 'bottom' | 'north' | 'south' | 'west' | 'east';

/** Deterministic pseudo random in 0..1 from a string seed, so scenery is stable between visits. */
export function seeded(seed: string): () => number {
  let h = 2166136261;
  for (let i = 0; i < seed.length; i++) {
    h ^= seed.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return () => {
    h += 0x6d2b79f5;
    let t = h;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
