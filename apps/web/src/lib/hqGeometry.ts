import type { FigureLook } from './hqAvatar.ts';
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
/** Floats per vertex: position 3, normal 3, colour 3, emissive 1, material 1, texture coordinates 2. */
export const VERTEX_FLOATS = 13;

/**
 * Surface classes. The renderer gives each one its own material (texture, roughness, reflection);
 * `flat` is plain painted colour. Surfaces with an emissive weight of 2 or more are windows whatever
 * their class: reflective glass by day, a warm lit pane at night (the part above 2 is the glow).
 */
export const MAT = {
  flat: 0, concrete: 1, tile: 2, asphalt: 3, grass: 4, wood: 5, metal: 6, water: 7, glass: 8, roller: 9, sign: 10, court: 11, paint: 12,
} as const;
export type MatId = (typeof MAT)[keyof typeof MAT];

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
  /** The surface class given to every triangle added until it is changed. */
  mat: MatId = MAT.flat;
  private data: number[] = [];
  /** Per triangle: the tag active when it was added (see `tagged`), or null. */
  private tags: (string | null)[] = [];
  /** The tag given to every triangle added until it is changed (see `tagged`). */
  tag: string | null = null;
  /** Placements of loadable models noted while building (see `hqModels.ts`); the pure scene hands them to the renderer. */
  readonly slots: ModelSlot[] = [];
  /** Soft light halos noted while building (street lamps, entrance lights): x, y, z, radius, r, g, b, strength per halo. */
  readonly halos: number[] = [];
  private lo = [Infinity, Infinity, Infinity];
  private hi = [-Infinity, -Infinity, -Infinity];

  /** Runs `fn` with the surface class set, then restores the previous one. */
  withMat(id: MatId, fn: () => void): void {
    const before = this.mat;
    this.mat = id;
    fn();
    this.mat = before;
  }

  /** Runs `fn` with a tag on every triangle it adds, so a caller can later split those triangles out (see `toFloat32Split`). */
  tagged(tag: string, fn: () => void): void {
    const before = this.tag;
    this.tag = tag;
    fn();
    this.tag = before;
  }

  /** Notes a soft light halo at a world point (drawn additively at night by the renderer). */
  halo(x: number, y: number, z: number, radius: number, color: Rgb, strength = 1): void {
    this.halos.push(x, y, z, radius, color[0], color[1], color[2], strength);
  }

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
    this.tags.push(this.tag);
    for (const p of [a, b, c]) {
      this.data.push(p[0], p[1], p[2], nx, ny, nz, color[0], color[1], color[2], emissive, this.mat, 0, 0);
      for (let i = 0; i < 3; i++) {
        if (p[i] < this.lo[i]) this.lo[i] = p[i];
        if (p[i] > this.hi[i]) this.hi[i] = p[i];
      }
    }
  }

  /** Triangle with texture coordinates (signs), flat normal. */
  triUV(
    a: readonly number[], b: readonly number[], c: readonly number[],
    uva: readonly [number, number], uvb: readonly [number, number], uvc: readonly [number, number], color: Rgb, emissive = 0,
  ): void {
    const ux = b[0] - a[0], uy = b[1] - a[1], uz = b[2] - a[2];
    const vx = c[0] - a[0], vy = c[1] - a[1], vz = c[2] - a[2];
    let nx = uy * vz - uz * vy, ny = uz * vx - ux * vz, nz = ux * vy - uy * vx;
    const len = Math.hypot(nx, ny, nz);
    if (len < 1e-12) return;
    nx /= len; ny /= len; nz /= len;
    const pts = [a, b, c], uvs = [uva, uvb, uvc];
    this.tags.push(this.tag);
    for (let k = 0; k < 3; k++) {
      const p = pts[k], uv = uvs[k];
      this.data.push(p[0], p[1], p[2], nx, ny, nz, color[0], color[1], color[2], emissive, this.mat, uv[0], uv[1]);
      for (let i = 0; i < 3; i++) {
        if (p[i] < this.lo[i]) this.lo[i] = p[i];
        if (p[i] > this.hi[i]) this.hi[i] = p[i];
      }
    }
  }

  /** Quad with texture coordinates: a, b, c, d in the same order as `quad`, one uv per corner. */
  quadUV(
    a: readonly number[], b: readonly number[], c: readonly number[], d: readonly number[],
    uva: readonly [number, number], uvb: readonly [number, number], uvc: readonly [number, number], uvd: readonly [number, number],
    color: Rgb, emissive = 0,
  ): void {
    this.triUV(a, b, c, uva, uvb, uvc, color, emissive);
    this.triUV(a, c, d, uva, uvc, uvd, color, emissive);
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
    this.tags.push(this.tag);
    for (let k = 0; k < 3; k++) {
      const p = pts[k], col = cols[k];
      this.data.push(p[0], p[1], p[2], nx, ny, nz, col[0], col[1], col[2], emissive, this.mat, 0, 0);
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

  /** Appends vertices from another builder's float array (whole triangles), keeping their classes and uv. */
  append(data: Float32Array): void {
    for (let i = 0; i < data.length; i += VERTEX_FLOATS * 3) this.tags.push(this.tag);
    for (let i = 0; i < data.length; i += VERTEX_FLOATS) {
      for (let k = 0; k < VERTEX_FLOATS; k++) this.data.push(data[i + k]);
      for (let a = 0; a < 3; a++) {
        const p = data[i + a];
        if (p < this.lo[a]) this.lo[a] = p;
        if (p > this.hi[a]) this.hi[a] = p;
      }
    }
  }

  toFloat32(): Float32Array {
    return new Float32Array(this.data);
  }

  /** The tag of each triangle, in order (null when untagged). */
  triangleTags(): readonly (string | null)[] {
    return this.tags;
  }

  /**
   * The triangles split by tag: `rest` holds every triangle whose tag is not in `tags`, and `byTag` the
   * triangles of each listed tag. A scene keeps `rest` as its mesh and ships each tagged part as the fallback
   * of a loadable model, drawn only when that model could not be loaded.
   */
  toFloat32Split(tags: readonly string[]): { rest: Float32Array; byTag: Map<string, Float32Array> } {
    const per = VERTEX_FLOATS * 3;
    const wanted = new Set(tags);
    const rest: number[] = [];
    const parts = new Map<string, number[]>(tags.map((t) => [t, []]));
    for (let t = 0; t < this.tags.length; t++) {
      const tag = this.tags[t];
      const target = tag !== null && wanted.has(tag) ? parts.get(tag)! : rest;
      for (let i = t * per; i < (t + 1) * per; i++) target.push(this.data[i]);
    }
    return { rest: new Float32Array(rest), byTag: new Map([...parts].map(([k, v]) => [k, new Float32Array(v)])) };
  }
}

/**
 * A place where a loadable model goes (a sofa, a seated person). `rot` is quarter turns as in the
 * interior frames: 0 faces +z, 1 faces -x, 2 faces -z, 3 faces +x. `width` is the footprint the
 * procedural piece was drawn with, so the loaded model is scaled to the same footprint.
 */
export type ModelSlot = Readonly<{
  model: string; x: number; y: number; z: number; rot: number; width: number;
  /** A seed for variants (a person's look); never a name. */
  seed?: string; pose?: string; headset?: boolean;
  /** For a person: the resolved avatar style (never from a name); absent means the default drawn from `seed`. */
  look?: FigureLook;
}>;

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
