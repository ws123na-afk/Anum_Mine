/**
 * Baked ambient occlusion and contact shadows for the headquarters scenes. Pure: works on the interleaved vertex arrays of
 * `hqGeometry.ts` (no three.js, no DOM), so the unit runner covers it and a renderer only multiplies the result into vertex
 * colours. The bake costs CPU once per scene (cached per vertex array) and nothing per frame.
 *
 * Vertex AO. Every triangle becomes a few area weighted occluder samples (a 3 by 3 barycentric grid for large ones). A vertex
 * sums the form factor of the samples inside `radius`: the sample must lie in front of the vertex normal and face the vertex,
 * so coplanar neighbours and the ground plane add nothing, while the inside of a corner, the underside of a balcony or the
 * space between two desks does. On top of that, walls darken towards the ground level they stand on (soft contact) and
 * soffits darken a little. Windows and lit surfaces are left alone.
 *
 * Contact shadows. Soft dark discs for things that stand on a floor: low objects (the side faces of anything 0.35 to 3.2 m
 * tall whose foot is on a ground level, such as cars, planters, benches, desks), and a canopy disc under every cluster of
 * foliage. They are drawn by the renderer as one flat transparent mesh.
 */
import { VERTEX_FLOATS, MAT } from './hqGeometry.ts';

export type AoOptions = Readonly<{ radius?: number; strength?: number; floor?: number; maxVertices?: number }>;
export const AO_DEFAULTS = { radius: 3, strength: 1.15, floor: 0.45, maxVertices: 500_000 } as const;

const TABLE = 1 << 17;
const cellHash = (x: number, y: number, z: number) => ((Math.imul(x, 73856093) ^ Math.imul(y, 19349663) ^ Math.imul(z, 83492791)) >>> 0) & (TABLE - 1);

/** Heights (y) with a large flat, upward facing area: the floors and the ground that things stand on, ascending. */
export function groundLevels(v: Float32Array, minArea = 30): number[] {
  const area = new Map<number, number>();
  const n = Math.floor(v.length / (VERTEX_FLOATS * 3));
  for (let t = 0; t < n; t++) {
    const o = t * 3 * VERTEX_FLOATS;
    if (v[o + 4] < 0.97 || v[o + 10] === MAT.water) continue;
    const y = v[o + 1];
    if (Math.abs(v[o + VERTEX_FLOATS + 1] - y) > 0.02 || Math.abs(v[o + 2 * VERTEX_FLOATS + 1] - y) > 0.02) continue;
    const ax = v[o + VERTEX_FLOATS] - v[o], az = v[o + VERTEX_FLOATS + 2] - v[o + 2], bx = v[o + 2 * VERTEX_FLOATS] - v[o], bz = v[o + 2 * VERTEX_FLOATS + 2] - v[o + 2];
    const a = Math.abs(ax * bz - az * bx) / 2;
    const key = Math.round(y * 10);
    area.set(key, (area.get(key) ?? 0) + a);
  }
  const keys = [...area].filter(([, a]) => a >= minArea).map(([k]) => k).sort((a, b) => a - b);
  // Merge levels within 0.2 m (floor plus its thin slab or a tile plane just above it).
  const out: number[] = [];
  for (const k of keys) if (!out.length || k - out[out.length - 1] > 2) out.push(k);
  return out.map((k) => k / 10);
}

type Samples = { x: Float32Array; y: Float32Array; z: Float32Array; nx: Float32Array; ny: Float32Array; nz: Float32Array; a: Float32Array; start: Int32Array; items: Int32Array; count: number };

function collectSamples(v: Float32Array, radius: number): Samples {
  const n = Math.floor(v.length / (VERTEX_FLOATS * 3));
  const xs: number[] = [], ys: number[] = [], zs: number[] = [], nxs: number[] = [], nys: number[] = [], nzs: number[] = [], as: number[] = [];
  for (let t = 0; t < n; t++) {
    const o = t * 3 * VERTEX_FLOATS;
    const em = v[o + 9], mat = v[o + 10];
    // Panes, signs and lit surfaces do not occlude; the flat ground at floor level never does (it is the baseline, not a blocker).
    if (em >= 2 || mat === MAT.sign || mat === MAT.water) continue;
    const ny = v[o + 4];
    const ax = v[o + VERTEX_FLOATS] - v[o], ay = v[o + VERTEX_FLOATS + 1] - v[o + 1], az = v[o + VERTEX_FLOATS + 2] - v[o + 2];
    const bx = v[o + 2 * VERTEX_FLOATS] - v[o], by = v[o + 2 * VERTEX_FLOATS + 1] - v[o + 1], bz = v[o + 2 * VERTEX_FLOATS + 2] - v[o + 2];
    const cx = ay * bz - az * by, cy = az * bx - ax * bz, cz = ax * by - ay * bx;
    const area = Math.hypot(cx, cy, cz) / 2;
    if (area < 0.004) continue;
    const y0 = Math.min(v[o + 1], v[o + VERTEX_FLOATS + 1], v[o + 2 * VERTEX_FLOATS + 1]);
    if (ny > 0.97 && y0 < 0.3 && area > 1) continue;
    const grid = area > 4 ? 3 : area > 1 ? 2 : 1;
    const share = area / (grid * grid);
    for (let i = 0; i < grid; i++) {
      for (let j = 0; j < grid - i; j++) {
        // Barycentric sample positions of a triangle subdivided into grid x grid cells (upright cells only: (grid * (grid + 1)) / 2 of them).
        const u = (i + 1 / 3) / grid, w = (j + 1 / 3) / grid;
        xs.push(v[o] + ax * u + bx * w); ys.push(v[o + 1] + ay * u + by * w); zs.push(v[o + 2] + az * u + bz * w);
        nxs.push(v[o + 3]); nys.push(ny); nzs.push(v[o + 5]); as.push(share * (grid * grid) / ((grid * (grid + 1)) / 2));
      }
    }
  }
  const count = xs.length;
  const start = new Int32Array(TABLE + 1);
  const cellOf = new Int32Array(count);
  for (let i = 0; i < count; i++) {
    const c = cellHash(Math.floor(xs[i] / radius), Math.floor(ys[i] / radius), Math.floor(zs[i] / radius));
    cellOf[i] = c; start[c + 1] += 1;
  }
  for (let i = 0; i < TABLE; i++) start[i + 1] += start[i];
  const fill = start.slice(0, TABLE);
  const items = new Int32Array(count);
  for (let i = 0; i < count; i++) items[fill[cellOf[i]]++] = i;
  return { x: Float32Array.from(xs), y: Float32Array.from(ys), z: Float32Array.from(zs), nx: Float32Array.from(nxs), ny: Float32Array.from(nys), nz: Float32Array.from(nzs), a: Float32Array.from(as), start, items, count };
}

/** One occlusion factor per vertex (1 = open, `floor` = fully closed in). Deterministic. */
export function bakeVertexAo(v: Float32Array, o: AoOptions = {}): Float32Array {
  const radius = o.radius ?? AO_DEFAULTS.radius, strength = o.strength ?? AO_DEFAULTS.strength, floor = o.floor ?? AO_DEFAULTS.floor;
  const total = Math.floor(v.length / VERTEX_FLOATS);
  const ao = new Float32Array(total).fill(1);
  if (total === 0 || total > (o.maxVertices ?? AO_DEFAULTS.maxVertices)) return ao;
  const s = collectSamples(v, radius);
  const levels = groundLevels(v);
  const r2 = radius * radius;
  // Vertices that share a position and facing (the corners of neighbouring quads) share their result.
  const memo = new Map<number, number>();
  for (let i = 0; i < total; i++) {
    const p = i * VERTEX_FLOATS;
    if (v[p + 9] >= 0.5 || v[p + 10] === MAT.sign || v[p + 10] === MAT.water) continue;
    const px = v[p], py = v[p + 1], pz = v[p + 2], nx = v[p + 3], ny = v[p + 4], nz = v[p + 5];
    const key = (Math.round(px * 8) * 73856093) ^ (Math.round(py * 8) * 19349663) ^ (Math.round(pz * 8) * 83492791) ^ (Math.round(nx * 2) * 1013) ^ (Math.round(ny * 2) * 2039) ^ (Math.round(nz * 2) * 4051);
    const hit = memo.get(key);
    if (hit !== undefined) { ao[i] = hit; continue; }
    const cx = Math.floor(px / radius), cy = Math.floor(py / radius), cz = Math.floor(pz / radius);
    let occ = 0;
    for (let dx = -1; dx <= 1; dx++) for (let dy = -1; dy <= 1; dy++) for (let dz = -1; dz <= 1; dz++) {
      const c = cellHash(cx + dx, cy + dy, cz + dz);
      for (let k = s.start[c]; k < s.start[c + 1]; k++) {
        const j = s.items[k];
        const ex = s.x[j] - px, ey = s.y[j] - py, ez = s.z[j] - pz;
        const d2 = ex * ex + ey * ey + ez * ez;
        if (d2 > r2 || d2 < 1e-6) continue;
        const d = Math.sqrt(d2);
        const cosV = (nx * ex + ny * ey + nz * ez) / d;
        if (cosV <= 0.05) continue;
        const cosO = -(s.nx[j] * ex + s.ny[j] * ey + s.nz[j] * ez) / d;
        if (cosO <= 0.05) continue;
        const f = 1 - d / radius;
        occ += Math.min(2, s.a[j]) * cosV * cosO * f * f / (d2 + 0.6);
      }
    }
    let value = 1 - Math.min(1, occ * strength * 0.5);
    // Walls darken towards the floor they stand on; soffits (faces looking down) are a little dim.
    if (Math.abs(ny) < 0.7 && levels.length) {
      let base = Number.NaN;
      for (const l of levels) if (l <= py + 0.05) base = l;
      if (!Number.isNaN(base)) value *= 1 - 0.3 * Math.exp(-(py - base) / 1.5);
    } else if (ny < -0.7) value *= 0.82;
    value = Math.max(floor, value);
    memo.set(key, value);
    ao[i] = value;
  }
  return ao;
}


/** Heights above a wall's base level at which tall wall triangles are cut, so the contact gradient is resolved where it matters. */
const CUTS = [0.6, 1.8, 3.6, 7.2, 14.4, 28.8, 57.6];

/**
 * Cuts tall, wall-like triangles (not panes) along horizontal planes above the level they stand on, interpolating every vertex
 * attribute. A plain 40 m wall is two triangles with vertices only at its corners, which could carry no gradient; after the cut
 * it has vertices at 0.6, 1.8, 3.6 m and so on. Returns the input when nothing needed cutting.
 */
export function refineWalls(v: Float32Array, levels: readonly number[] = groundLevels(v)): Float32Array {
  const n = Math.floor(v.length / (VERTEX_FLOATS * 3));
  const parts: Float32Array[] = [];
  let changed = false;
  let runStart = 0;
  const F = VERTEX_FLOATS;
  for (let t = 0; t < n; t++) {
    const o = t * 3 * F;
    const ys = [v[o + 1], v[o + F + 1], v[o + 2 * F + 1]];
    const lo = Math.min(...ys), hi = Math.max(...ys);
    if (Math.abs(v[o + 4]) >= 0.7 || v[o + 9] >= 2 || hi - lo < 1.5 || v[o + 10] === MAT.sign || v[o + 10] === MAT.foliage) continue;
    let base = Number.NaN;
    for (const l of levels) if (l <= lo + 0.05) base = l;
    if (Number.isNaN(base)) continue;
    const planes = CUTS.map((c) => base + c).filter((y) => y > lo + 0.05 && y < hi - 0.05);
    if (!planes.length) continue;
    // Clip the triangle against each slab between consecutive planes and fan the polygon.
    const edges = [lo - 1, ...planes, hi + 1];
    const out: number[] = [];
    for (let s = 0; s + 1 < edges.length; s++) {
      let poly: number[][] = [0, 1, 2].map((k) => Array.from(v.subarray(o + k * F, o + (k + 1) * F)));
      for (const [plane, keepAbove] of [[edges[s], true], [edges[s + 1], false]] as const) {
        const next: number[][] = [];
        for (let i = 0; i < poly.length; i++) {
          const a = poly[i], b = poly[(i + 1) % poly.length];
          const ina = keepAbove ? a[1] >= plane : a[1] <= plane, inb = keepAbove ? b[1] >= plane : b[1] <= plane;
          if (ina) next.push(a);
          if (ina !== inb) { const k = (plane - a[1]) / (b[1] - a[1]); next.push(a.map((x, j) => x + (b[j] - x) * k)); }
        }
        poly = next;
      }
      for (let i = 1; i + 1 < poly.length; i++) out.push(...poly[0], ...poly[i], ...poly[i + 1]);
    }
    if (o > runStart) parts.push(v.subarray(runStart, o));
    parts.push(Float32Array.from(out));
    runStart = o + 3 * F;
    changed = true;
  }
  if (!changed) return v;
  if (runStart < v.length) parts.push(v.subarray(runStart));
  const total = parts.reduce((m, p) => m + p.length, 0);
  const merged = new Float32Array(total);
  let at = 0;
  for (const p of parts) { merged.set(p, at); at += p.length; }
  return merged;
}

/** The scene's geometry with tall walls cut (see `refineWalls`) and one occlusion factor per vertex of it. */
export function bakeScene(v: Float32Array, o: AoOptions = {}): { vertices: Float32Array; ao: Float32Array } {
  const vertices = refineWalls(v);
  return { vertices, ao: bakeVertexAo(vertices, o) };
}

/** A soft dark disc on the floor: x, y, z (the floor), radius, strength per entry. */
export const CONTACT_FLOATS = 5;
export const MAX_CONTACTS = 2600;

/** Contact discs for low objects and foliage canopies (see the file header). Deterministic, at most `MAX_CONTACTS`, strongest first. */
export function deriveContacts(v: Float32Array): Float32Array {
  const levels = groundLevels(v);
  const n = Math.floor(v.length / (VERTEX_FLOATS * 3));
  const levelUnder = (y: number): number | null => { let b: number | null = null; for (const l of levels) if (l <= y + 0.12) b = l; return b; };
  // (a) Low objects: side faces standing on a level, binned in 1.2 m cells.
  type Bin = { w: number; x: number; z: number; y: number };
  const bins = new Map<number, Bin>();
  // (b) Foliage: 2.5 m cells joined by neighbours into canopies.
  const strips: Array<[number, number, number, number, number]> = [];
  const leaf = new Map<number, { minX: number; maxX: number; minZ: number; maxZ: number; minY: number; cx: number; cz: number }>();
  for (let t = 0; t < n; t++) {
    const o = t * 3 * VERTEX_FLOATS;
    const mat = v[o + 10];
    const xs = [v[o], v[o + VERTEX_FLOATS], v[o + 2 * VERTEX_FLOATS]], ys = [v[o + 1], v[o + VERTEX_FLOATS + 1], v[o + 2 * VERTEX_FLOATS + 1]], zs = [v[o + 2], v[o + VERTEX_FLOATS + 2], v[o + 2 * VERTEX_FLOATS + 2]];
    const y0 = Math.min(...ys), y1 = Math.max(...ys);
    const mx = (xs[0] + xs[1] + xs[2]) / 3, mz = (zs[0] + zs[1] + zs[2]) / 3;
    if (mat === MAT.foliage) {
      const key = Math.floor(mx / 2.5) * 100003 + Math.floor(mz / 2.5);
      const e = leaf.get(key) ?? { minX: Infinity, maxX: -Infinity, minZ: Infinity, maxZ: -Infinity, minY: Infinity, cx: Math.floor(mx / 2.5), cz: Math.floor(mz / 2.5) };
      e.minX = Math.min(e.minX, ...xs); e.maxX = Math.max(e.maxX, ...xs); e.minZ = Math.min(e.minZ, ...zs); e.maxZ = Math.max(e.maxZ, ...zs); e.minY = Math.min(e.minY, y0);
      leaf.set(key, e);
      continue;
    }
    if (Math.abs(v[o + 4]) > 0.3 || v[o + 9] >= 2) continue;
    const h = y1 - y0;
    const base = levelUnder(y0);
    if (base === null || y0 - base > 0.12 || y1 - base < 0.35 || h < 0.05) continue;
    if (y1 - base > 3.2) {
      // A building or room wall: soft strips on the floor along its foot, every 2.5 m, pushed 0.9 m out along the face normal.
      const onFloor = [0, 1, 2].filter((k) => ys[k] - base < 0.12);
      if (onFloor.length === 2) {
        const [p, q] = onFloor, len = Math.hypot(xs[q] - xs[p], zs[q] - zs[p]);
        const nx = v[o + 3], nz = v[o + 5];
        for (let d = 1.2; d < len; d += 2.5) {
          const f = d / len;
          strips.push([xs[p] + (xs[q] - xs[p]) * f + nx * 0.9, base, zs[p] + (zs[q] - zs[p]) * f + nz * 0.9, 2.1, 0.24]);
        }
      }
      continue;
    }
    const ax = xs[1] - xs[0], ay = ys[1] - ys[0], az = zs[1] - zs[0], bx = xs[2] - xs[0], by = ys[2] - ys[0], bz = zs[2] - zs[0];
    const area = Math.hypot(ay * bz - az * by, az * bx - ax * bz, ax * by - ay * bx) / 2;
    const key = Math.floor(mx / 1.2) * 100003 + Math.floor(mz / 1.2);
    const b = bins.get(key) ?? { w: 0, x: 0, z: 0, y: base };
    b.x += mx * area; b.z += mz * area; b.w += area; b.y = base;
    bins.set(key, b);
  }
  const out: Array<[number, number, number, number, number]> = [];
  for (const b of bins.values()) if (b.w >= 0.6) out.push([b.x / b.w, b.y, b.z / b.w, 0.9 + 0.35 * Math.sqrt(b.w), Math.min(0.5, 0.18 + 0.05 * b.w)]);
  out.push(...strips);
  // Join foliage cells into canopies (8 neighbours), one disc per canopy; very long canopies (hedges) keep one disc per cell.
  const seen = new Set<number>();
  for (const [key, first] of leaf) {
    if (seen.has(key)) continue;
    const stack = [first]; seen.add(key);
    const cells: typeof first[] = [];
    while (stack.length) {
      const e = stack.pop()!;
      cells.push(e);
      for (let dx = -1; dx <= 1; dx++) for (let dz = -1; dz <= 1; dz++) {
        const k = (e.cx + dx) * 100003 + (e.cz + dz);
        const nb = leaf.get(k);
        if (nb && !seen.has(k)) { seen.add(k); stack.push(nb); }
      }
    }
    const minX = Math.min(...cells.map((c) => c.minX)), maxX = Math.max(...cells.map((c) => c.maxX)), minZ = Math.min(...cells.map((c) => c.minZ)), maxZ = Math.max(...cells.map((c) => c.maxZ));
    const minY = Math.min(...cells.map((c) => c.minY));
    const base = levelUnder(minY) ?? levelUnder(minY + 6) ?? 0;
    if (Math.max(maxX - minX, maxZ - minZ) > 14) {
      for (const c of cells) out.push([(c.minX + c.maxX) / 2, base, (c.minZ + c.maxZ) / 2, Math.max(c.maxX - c.minX, c.maxZ - c.minZ) * 0.7 + 0.6, 0.2]);
    } else out.push([(minX + maxX) / 2, base, (minZ + maxZ) / 2, Math.max(maxX - minX, maxZ - minZ) * 0.62 + 0.5, 0.3]);
  }
  out.sort((a, b) => b[4] * b[3] - a[4] * a[3] || a[0] - b[0] || a[2] - b[2]);
  const kept = out.slice(0, MAX_CONTACTS);
  const flat = new Float32Array(kept.length * CONTACT_FLOATS);
  kept.forEach((e, i) => flat.set(e, i * CONTACT_FLOATS));
  return flat;
}
