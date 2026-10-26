/**
 * Geometry detail derived from the scene's own triangles, for the photographic tier. Pure (no three.js, no DOM); the result is one
 * extra opaque item that the renderer adds on top of a campus scene. Everything here is scenery: roofs, kerbs and planters. It never
 * draws a person and encodes no name, count or status.
 *
 * - Roof coping: a stone cap (0.5 m wide, 0.22 m high) along every real edge of a roof. Edges come from the triangles at one roof
 *   height: an edge used once is a candidate; it is kept only when exactly one side of it is covered by roof (a seam between two roof
 *   pieces has roof on both sides). Where a building already draws its own parapet the cap simply sits on the edge.
 * - Rooftop plant: air conditioning units, vent stacks and plant housings on free roof area. Free means no other triangle between the
 *   roof and 3.2 m above it, at least 1 m from the roof edge, and not on a cell an earlier unit took.
 * - Planters: concrete troughs with a clipped shrub along the foot of ground floor walls, every 12 m, never next to anything lit
 *   (an entrance) or on something already standing there.
 */
import { MeshBuilder, MAT, VERTEX_FLOATS, hex, mix, scale, seeded, type Rgb } from './hqGeometry.ts';
import { groundLevels } from './hqBakedAo.ts';

export const DETAIL_TRIANGLES_CAP = 2600;
export type DetailResult = Readonly<{ vertices: Float32Array; coping: number; units: number; planters: number; triangles: number }>;

const STONE: Rgb = hex('#cfc8b8'), UNIT: Rgb = hex('#aeb6bd'), DARK: Rgb = hex('#3a4148'), PLANTER: Rgb = hex('#bdb6a6'), SHRUB: Rgb = hex('#2e5f38');

type Tri = { x: number[]; y: number[]; z: number[]; color: Rgb; em: number; mat: number; ny: number };

function readTris(v: Float32Array): Tri[] {
  const out: Tri[] = [];
  const n = Math.floor(v.length / (VERTEX_FLOATS * 3));
  for (let t = 0; t < n; t++) {
    const o = t * 3 * VERTEX_FLOATS, F = VERTEX_FLOATS;
    out.push({
      x: [v[o], v[o + F], v[o + 2 * F]], y: [v[o + 1], v[o + F + 1], v[o + 2 * F + 1]], z: [v[o + 2], v[o + F + 2], v[o + 2 * F + 2]],
      color: [v[o + 6], v[o + 7], v[o + 8]], em: v[o + 9], mat: v[o + 10], ny: v[o + 4],
    });
  }
  return out;
}

const area2 = (t: Tri) => Math.abs((t.x[1] - t.x[0]) * (t.z[2] - t.z[0]) - (t.z[1] - t.z[0]) * (t.x[2] - t.x[0])) / 2;

function inTri(t: Tri, px: number, pz: number): boolean {
  const d = (ax: number, az: number, bx: number, bz: number) => (px - bx) * (az - bz) - (ax - bx) * (pz - bz);
  const d1 = d(t.x[0], t.z[0], t.x[1], t.z[1]), d2 = d(t.x[1], t.z[1], t.x[2], t.z[2]), d3 = d(t.x[2], t.z[2], t.x[0], t.z[0]);
  return !((d1 < 0 || d2 < 0 || d3 < 0) && (d1 > 0 || d2 > 0 || d3 > 0));
}

/** A grid over triangles so "is this point on the roof" is a few tests, not all of them. */
function coverage(tris: readonly Tri[]): (x: number, z: number) => boolean {
  const cell = 4, grid = new Map<number, Tri[]>();
  for (const t of tris) {
    const x0 = Math.floor(Math.min(...t.x) / cell), x1 = Math.floor(Math.max(...t.x) / cell), z0 = Math.floor(Math.min(...t.z) / cell), z1 = Math.floor(Math.max(...t.z) / cell);
    for (let i = x0; i <= x1; i++) for (let j = z0; j <= z1; j++) { const k = i * 100003 + j; (grid.get(k) ?? grid.set(k, []).get(k)!).push(t); }
  }
  return (x, z) => (grid.get(Math.floor(x / cell) * 100003 + Math.floor(z / cell)) ?? []).some((t) => inTri(t, x, z));
}

/** A stone cap along the segment a to b (x, z), `w` wide, from y to y + h. 10 triangles. */
function cap(m: MeshBuilder, ax: number, az: number, bx: number, bz: number, y: number, w: number, h: number, color: Rgb): void {
  const len = Math.hypot(bx - ax, bz - az);
  if (len < 0.2) return;
  const dx = (bx - ax) / len, dz = (bz - az) / len, nx = -dz * (w / 2), nz = dx * (w / 2);
  const p = (px: number, pz: number, py: number): number[] => [px, py, pz];
  const c0 = [ax + nx, az + nz], c1 = [bx + nx, bz + nz], c2 = [bx - nx, bz - nz], c3 = [ax - nx, az - nz];
  const top = y + h;
  m.quad(p(c0[0], c0[1], top), p(c3[0], c3[1], top), p(c2[0], c2[1], top), p(c1[0], c1[1], top), scale(color, 1.04));
  m.quad(p(c1[0], c1[1], y), p(c2[0], c2[1], y), p(c2[0], c2[1], top), p(c1[0], c1[1], top), scale(color, 0.86));
  m.quad(p(c3[0], c3[1], y), p(c0[0], c0[1], y), p(c0[0], c0[1], top), p(c3[0], c3[1], top), scale(color, 0.8));
  m.quad(p(c0[0], c0[1], y), p(c1[0], c1[1], y), p(c1[0], c1[1], top), p(c0[0], c0[1], top), scale(color, 0.92));
  m.quad(p(c2[0], c2[1], y), p(c3[0], c3[1], y), p(c3[0], c3[1], top), p(c2[0], c2[1], top), scale(color, 0.92));
}

const q = (x: number) => Math.round(x * 50);

export function deriveDetail(v: Float32Array, o: { seed?: string; maxTriangles?: number } = {}): DetailResult {
  const rnd = seeded(`detail:${o.seed ?? 'campus'}`);
  const m = new MeshBuilder();
  const cap_ = o.maxTriangles ?? DETAIL_TRIANGLES_CAP;
  const tris = readTris(v);
  const room = () => m.triangleCount < cap_ - 40;
  let coping = 0, units = 0, planters = 0;

  // Roofs: horizontal, upward facing triangles at least 3.5 m up, grouped by height.
  const roofs = new Map<number, Tri[]>();
  for (const t of tris) {
    if (t.ny < 0.97 || t.y[0] < 3.5 || Math.max(...t.y) - Math.min(...t.y) > 0.02 || t.mat === MAT.water || t.mat === MAT.sign || t.em >= 2 || area2(t) < 0.05) continue;
    const key = Math.round(t.y[0] * 20);
    (roofs.get(key) ?? roofs.set(key, []).get(key)!).push(t);
  }
  const groups = [...roofs].map(([key, g]) => ({ y: key / 20, tris: g, area: g.reduce((a, t) => a + area2(t), 0) })).filter((g) => g.area >= 40).sort((a, b) => b.area - a.area || a.y - b.y).slice(0, 14);
  for (const g of groups) {
    const covered = coverage(g.tris);
    const edges = new Map<string, { n: number; a: [number, number]; b: [number, number] }>();
    for (const t of g.tris) for (let i = 0; i < 3; i++) {
      const j = (i + 1) % 3;
      const a: [number, number] = [t.x[i], t.z[i]], b: [number, number] = [t.x[j], t.z[j]];
      const ka = `${q(a[0])},${q(a[1])}`, kb = `${q(b[0])},${q(b[1])}`;
      const key = ka < kb ? `${ka}|${kb}` : `${kb}|${ka}`;
      const e = edges.get(key);
      if (e) e.n += 1; else edges.set(key, { n: 1, a, b });
    }
    const color = mix(UNIT, g.tris[0].color, 0.25);
    const boundary: Array<[number, number, number, number]> = [];
    for (const e of edges.values()) {
      const len = Math.hypot(e.b[0] - e.a[0], e.b[1] - e.a[1]);
      if (e.n !== 1 || len < 1.2) continue;
      const mx = (e.a[0] + e.b[0]) / 2, mz = (e.a[1] + e.b[1]) / 2, nx = -(e.b[1] - e.a[1]) / len, nz = (e.b[0] - e.a[0]) / len;
      if (covered(mx + nx * 0.2, mz + nz * 0.2) === covered(mx - nx * 0.2, mz - nz * 0.2)) continue;
      boundary.push([e.a[0], e.a[1], e.b[0], e.b[1]]);
    }
    boundary.sort((a, b) => a[0] - b[0] || a[1] - b[1] || a[2] - b[2]);
    m.withMat(MAT.concrete, () => {
      for (const [ax, az, bx, bz] of boundary.slice(0, 60)) { if (!room()) break; cap(m, ax, az, bx, bz, g.y, 0.5, 0.22, mix(STONE, color, 0.2)); coping += 1; }
    });
    if (g.area < 70 || g.y < 6) continue;
    // Free space: cells (1 m) taken by any other triangle standing on the roof, or within 1 m of an edge.
    const taken = new Set<number>();
    const mark = (x0: number, x1: number, z0: number, z1: number) => { for (let i = Math.floor(x0); i <= Math.floor(x1); i++) for (let j = Math.floor(z0); j <= Math.floor(z1); j++) taken.add(i * 100003 + j); };
    const minX = Math.min(...g.tris.flatMap((t) => t.x)), maxX = Math.max(...g.tris.flatMap((t) => t.x)), minZ = Math.min(...g.tris.flatMap((t) => t.z)), maxZ = Math.max(...g.tris.flatMap((t) => t.z));
    for (const t of tris) {
      const lo = Math.min(...t.y), hi = Math.max(...t.y);
      if (hi < g.y + 0.05 || lo > g.y + 3.2 || (t.ny > 0.97 && Math.abs(lo - g.y) < 0.03)) continue;
      const x0 = Math.min(...t.x), x1 = Math.max(...t.x), z0 = Math.min(...t.z), z1 = Math.max(...t.z);
      if (x1 < minX - 1 || x0 > maxX + 1 || z1 < minZ - 1 || z0 > maxZ + 1) continue;
      mark(x0 - 0.4, x1 + 0.4, z0 - 0.4, z1 + 0.4);
    }
    for (const [ax, az, bx, bz] of boundary) {
      const len = Math.hypot(bx - ax, bz - az);
      for (let d = 0; d <= len; d += 0.5) mark(ax + ((bx - ax) * d) / len - 1, ax + ((bx - ax) * d) / len + 1, az + ((bz - az) * d) / len - 1, az + ((bz - az) * d) / len + 1);
    }
    const want = Math.min(8, Math.ceil(g.area / 70));
    let placed = 0;
    for (let i = 0; i < 40 && placed < want && room(); i++) {
      const x = minX + 1.5 + rnd() * Math.max(0.1, maxX - minX - 3), z = minZ + 1.5 + rnd() * Math.max(0.1, maxZ - minZ - 3);
      const kind = rnd();
      const w = kind < 0.5 ? 1.5 : kind < 0.8 ? 0.5 : 2.2, d = kind < 0.5 ? 1.0 : kind < 0.8 ? 0.5 : 1.6;
      const x0 = x - w / 2, z0 = z - d / 2;
      if (![[x0, z0], [x0 + w, z0], [x0, z0 + d], [x0 + w, z0 + d], [x, z]].every(([cx, cz]) => covered(cx, cz))) continue;
      let free = true;
      for (let a = Math.floor(x0 - 0.3); a <= Math.floor(x0 + w + 0.3) && free; a++) for (let b = Math.floor(z0 - 0.3); b <= Math.floor(z0 + d + 0.3); b++) if (taken.has(a * 100003 + b)) { free = false; break; }
      if (!free) continue;
      mark(x0 - 0.3, x0 + w + 0.3, z0 - 0.3, z0 + d + 0.3);
      m.withMat(MAT.metal, () => {
        if (kind < 0.5) {
          m.box(x0, g.y, z0, w, 0.95, d, UNIT, { skip: ['bottom'] });
          m.disc(x0 + w * 0.3, g.y + 0.96, z0 + d / 2, 0.34, 8, DARK); m.disc(x0 + w * 0.72, g.y + 0.96, z0 + d / 2, 0.34, 8, DARK);
        } else if (kind < 0.8) {
          m.prism(x, g.y, z, 0.2, 0.2, 1.7, 6, scale(UNIT, 0.9)); m.prism(x, g.y + 1.7, z, 0.34, 0.06, 0.2, 6, UNIT);
        } else {
          m.box(x0, g.y, z0, w, 1.5, d, scale(UNIT, 0.95), { skip: ['bottom'] });
          for (let k = 0; k < 4; k++) m.box(x0 + 0.15 + k * (w - 0.3) / 4, g.y + 0.35, z0 + d - 0.005, (w - 0.3) / 4 - 0.08, 0.7, 0.02, DARK);
        }
      });
      placed += 1; units += 1;
    }
  }

  // Planters along the foot of ground floor walls.
  const levels = groundLevels(v);
  const ground = levels.length ? levels[0] : 0;
  const blocked = new Set<number>();
  const lit = new Set<number>();
  for (const t of tris) {
    const lo = Math.min(...t.y), hi = Math.max(...t.y);
    const x0 = Math.floor(Math.min(...t.x)), x1 = Math.floor(Math.max(...t.x)), z0 = Math.floor(Math.min(...t.z)), z1 = Math.floor(Math.max(...t.z));
    if (t.em >= 0.3 && t.em < 2 && lo < ground + 3.5) { for (let i = x0 - 3; i <= x1 + 3; i++) for (let j = z0 - 3; j <= z1 + 3; j++) lit.add(i * 100003 + j); }
    if (hi - ground > 0.25 && lo - ground < 0.8 && Math.abs(t.ny) < 0.97 && hi - lo < 3.2) { for (let i = x0 - 1; i <= x1 + 1; i++) for (let j = z0 - 1; j <= z1 + 1; j++) blocked.add(i * 100003 + j); }
  }
  const feet: Array<{ ax: number; az: number; bx: number; bz: number; nx: number; nz: number }> = [];
  for (let k = 0; k < tris.length; k++) {
    const t = tris[k];
    const top = Math.max(...t.y) - ground;
    if (Math.abs(t.ny) >= 0.3 || top <= 3.2 || top >= 80 || t.em >= 2 || t.mat === MAT.sign) continue;
    const base = [0, 1, 2].filter((i) => t.y[i] - ground < 0.6);
    if (base.length !== 2) continue;
    const [i0, i1] = base;
    feet.push({ ax: t.x[i0], az: t.z[i0], bx: t.x[i1], bz: t.z[i1], nx: v[k * 3 * VERTEX_FLOATS + 3], nz: v[k * 3 * VERTEX_FLOATS + 5] });
  }
  feet.sort((a, b) => a.ax - b.ax || a.az - b.az || a.bx - b.bx);
  let lastKey = '';
  for (const f of feet) {
    const len = Math.hypot(f.bx - f.ax, f.bz - f.az);
    if (len < 6) continue;
    for (let d = 3 + rnd() * 3; d < len - 3 && planters < 120 && room(); d += 12) {
      const fx = f.ax + ((f.bx - f.ax) * d) / len + f.nx * 0.85, fz = f.az + ((f.bz - f.az) * d) / len + f.nz * 0.85;
      const key = `${Math.round(fx / 2)},${Math.round(fz / 2)}`;
      const cellKey = Math.floor(fx) * 100003 + Math.floor(fz);
      if (key === lastKey || lit.has(cellKey) || blocked.has(cellKey)) continue;
      lastKey = key;
      const ux = (f.bx - f.ax) / len, uz = (f.bz - f.az) / len;
      m.withMat(MAT.concrete, () => {
        cap(m, fx - ux * 0.6, fz - uz * 0.6, fx + ux * 0.6, fz + uz * 0.6, ground, 0.55, 0.5, PLANTER);
      });
      m.withMat(MAT.foliage, () => m.smoothBlob(fx, ground + 0.78, fz, 0.7, 0.4, 0.34, SHRUB, { lat: 3, lon: 7, lump: 0.1, seed: planters + 3 }));
      planters += 1;
    }
  }
  const out = m.toFloat32();
  return { vertices: out, coping, units, planters, triangles: m.triangleCount };
}
