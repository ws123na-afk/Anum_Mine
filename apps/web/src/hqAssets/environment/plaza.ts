/**
 * The campus heart and the building surrounds: a globe monument in a pool on a paved plaza, the big multi tier fountain plaza
 * with its roundabout and avenues (`centerPlaza`, `roundabout`, `avenue`), and a garden
 * for each building (paved apron with a paving pattern, clipped hedges, lawn beds with trees and shrubs).
 * Decoration only: the globe is a plain world map in two blues and greens, with no text and no data.
 */
import { MeshBuilder, MAT, hex, mix, scale, seeded, type Rgb } from '../../lib/hqGeometry.ts';
import { STONE, TEAL, WARM, flatPoly } from '../../lib/hqProps.ts';
import { scoped, type AssetResult, type GlowPoint } from './common.ts';
import { circlePath, ribbon, type P2 } from './paths.ts';
import { road, ROAD_Y } from './roads.ts';
import { bench, lamp, lampLine } from './furniture.ts';
import { hedgeRun, shrub, topiary, tree } from './planting.ts';

const COPING = hex('#d6d0c1');
const POOL_WALL = hex('#a9a395');
const OCEAN = hex('#2f78c4');
const LAND = hex('#4aa06a');

/** A smooth world map sphere (ocean and land in two blues and greens) lit from inside at night, with two thin lit rings. Decoration only. */
export function globe(m: MeshBuilder, cx: number, gy: number, cz: number, gr: number): void {
  // The globe: smooth normals, ocean and land colours, lit from inside at night.
  const LAT = 10, LON = 20;
  const vert = (i: number, j: number) => {
    const la = -Math.PI / 2 + (i / LAT) * Math.PI, lo = (j / LON) * Math.PI * 2;
    const n = [Math.cos(la) * Math.cos(lo), Math.sin(la), Math.cos(la) * Math.sin(lo)];
    return { p: [cx + n[0] * gr, gy + n[1] * gr, cz + n[2] * gr], n };
  };
  m.withMat(MAT.paint, () => {
    for (let i = 0; i < LAT; i++) {
      for (let j = 0; j < LON; j++) {
        const la = -Math.PI / 2 + ((i + 0.5) / LAT) * Math.PI, lo = ((j + 0.5) / LON) * Math.PI * 2;
        const land = isLand(la, lo);
        const col = land ? mix(LAND, hex('#8fc47a'), 0.3 * Math.sin(lo * 3 + la * 2) ** 2) : mix(OCEAN, hex('#6cb4ee'), 0.35 * Math.max(0, Math.cos(la)));
        const a = vert(i, j), c = vert(i, j + 1), d = vert(i + 1, j + 1), e = vert(i + 1, j);
        const em = land ? 0.3 : 0.55;
        if (i > 0) m.triN(a.p, d.p, c.p, a.n, d.n, c.n, col, col, col, em);
        if (i < LAT - 1) m.triN(a.p, e.p, d.p, a.n, e.n, d.n, col, col, col, em);
      }
    }
  });
  // Two thin lit rings round the globe (a tilted equator and a meridian).
  for (const tilt of [0.42, 1.9]) {
    const ringPts = (k: number) => {
      const a = (k / 28) * Math.PI * 2, r = gr + 0.22;
      const x = Math.cos(a) * r, y = Math.sin(a) * r;
      return [cx + x * Math.cos(tilt), gy + y, cz + x * Math.sin(tilt)];
    };
    for (let k = 0; k < 28; k++) {
      const p = ringPts(k), q = ringPts(k + 1);
      m.quad([p[0], p[1] - 0.05, p[2]], [q[0], q[1] - 0.05, q[2]], [q[0], q[1] + 0.05, q[2]], [p[0], p[1] + 0.05, p[2]], hex('#cfeaff'), 0.95);
      m.quad([p[0], p[1] + 0.05, p[2]], [q[0], q[1] + 0.05, q[2]], [q[0], q[1] - 0.05, q[2]], [p[0], p[1] - 0.05, p[2]], hex('#cfeaff'), 0.95);
    }
  }
}

export const GLOBE_MONUMENT_TRIANGLES_CAP = 2400;

/** One world map cell: land where two low-frequency waves agree. Deterministic and purely decorative. */
function isLand(lat: number, lon: number): boolean {
  const v = Math.sin(lon * 2.1 + 0.6) * Math.cos(lat * 2.6 - 0.3) + 0.55 * Math.sin(lon * 4.7 - lat * 3.1 + 1.2) + 0.25 * Math.cos(lon * 7.3 + lat * 5.9);
  return v > 0.55;
}

/** A globe on a pedestal in a round pool: paving rings, a coping ring, water jets, two lit meridian rings and a night glow. */
export function globeMonument(b: MeshBuilder, cx: number, cz: number, o: { plazaRadius?: number } = {}): AssetResult {
  const R = o.plazaRadius ?? 17;
  return scoped(b, [], (m) => {
    // Paving: concentric bands in alternating tones, so the plaza reads as laid stone and not as a flat disc.
    m.withMat(MAT.tile, () => {
      const bands: Array<[number, Rgb]> = [[R, mix(STONE, hex('#ffffff'), 0.22)], [R * 0.86, scale(STONE, 0.84)], [R * 0.74, mix(STONE, hex('#ffffff'), 0.3)], [R * 0.6, scale(STONE, 0.78)]];
      bands.forEach(([r, c], i) => m.disc(cx, 0.02 + i * 0.004, cz, r, 40, c));
      // Radial joints between the paving bands.
      for (let i = 0; i < 24; i++) {
        const a = (i / 24) * Math.PI * 2, c = Math.cos(a), s = Math.sin(a), w = 0.06;
        flatPoly(m, [[cx + c * R * 0.62 - s * w, cz + s * R * 0.62 + c * w], [cx + c * R * 0.98 - s * w, cz + s * R * 0.98 + c * w], [cx + c * R * 0.98 + s * w, cz + s * R * 0.98 - c * w], [cx + c * R * 0.62 + s * w, cz + s * R * 0.62 - c * w]], 0.04, scale(STONE, 0.7));
      }
    });
    // Pool: water, an inner wall and a raised coping ring.
    const pool = circlePath(cx, cz, 9.2, 36);
    m.withMat(MAT.water, () => m.disc(cx, 0.1, cz, 9.0, 36, hex('#2a7ca8'), 0.08));
    m.withMat(MAT.concrete, () => {
      ribbon(m, pool, true, 0, 0.1, 0, 0.5, POOL_WALL, 'left');
      ribbon(m, pool, true, 0, 0.5, 0.7, 0.5, COPING);
      ribbon(m, pool, true, 0.7, 0.5, 0.7, 0.02, scale(STONE, 0.9), 'right');
    });
    // Plinth steps and the pedestal.
    m.withMat(MAT.concrete, () => {
      m.prism(cx, 0.1, cz, 4.8, 4.8, 0.9, 28, mix(STONE, hex('#ffffff'), 0.2));
      m.prism(cx, 1.0, cz, 3.7, 3.5, 0.55, 28, COPING);
      m.prism(cx, 1.55, cz, 1.5, 1.2, 1.5, 12, scale(STONE, 0.78));
    });
    m.prism(cx, 3.0, cz, 2.1, 2.1, 0.16, 28, hex('#d9b25f'), { emissive: 0.9 });
    const gy = 6.3, gr = 3.1;
    globe(m, cx, gy, cz, gr);
    // Water jets round the pool and lit rim studs.
    for (let i = 0; i < 14; i++) {
      const a = (i / 14) * Math.PI * 2;
      m.prism(cx + Math.cos(a) * 7.4, 0.1, cz + Math.sin(a) * 7.4, 0.14, 0.03, 2.0 + (i % 2) * 0.7, 5, hex('#dff3ff'), { emissive: 0.9, capTop: false });
      m.prism(cx + Math.cos(a + 0.22) * 9.6, 0.5, cz + Math.sin(a + 0.22) * 9.6, 0.09, 0.09, 0.14, 5, TEAL, { emissive: 0.95 });
    }
    // Light pools: the globe, the pool rim and the plaza.
    b.halo(cx, gy, cz, 17, hex('#7fc4ff'), 1.5);
    b.halo(cx, 0.8, cz, 15, hex('#6fd0e8'), 0.8);
    for (let i = 0; i < 6; i++) {
      const a = (i / 6) * Math.PI * 2 + 0.3;
      b.halo(cx + Math.cos(a) * 12.5, 0.9, cz + Math.sin(a) * 12.5, 5.5, WARM, 0.7);
    }
  });
}

// ---------------------------------------------------------------- the centre plaza

export const CENTER_PLAZA_TRIANGLES_CAP = 6400;
export const ROUNDABOUT_TRIANGLES_CAP = 1900;
export const AVENUE_TRIANGLES_CAP = 700;

const JET = hex('#d5f1ff');
const LIT_WATER = hex('#2a86b4');
const DEEP_WATER = hex('#1d6a93');
const GOLD = hex('#d9b25f');

/** One jet of water: a thin ribbon bent through the points `pts` (x, y, z), drawn from both sides so it never vanishes edge on. */
function jetArc(m: MeshBuilder, cx: number, cz: number, angle: number, pts: ReadonlyArray<readonly [number, number]>, w0: number, w1: number): void {
  const c = Math.cos(angle), s = Math.sin(angle);
  const at = (r: number, y: number): [number, number, number] => [cx + c * r, y, cz + s * r];
  for (let i = 0; i + 1 < pts.length; i++) {
    const p = at(pts[i][0], pts[i][1]), q = at(pts[i + 1][0], pts[i + 1][1]);
    const wa = w0 + (w1 - w0) * (i / (pts.length - 1)), wb = w0 + (w1 - w0) * ((i + 1) / (pts.length - 1));
    const sx = -s * wa, sz = c * wa, tx = -s * wb, tz = c * wb;
    m.quad([p[0] - sx, p[1], p[2] - sz], [p[0] + sx, p[1], p[2] + sz], [q[0] + tx, q[1], q[2] + tz], [q[0] - tx, q[1], q[2] - tz], JET, 0.6);
    m.quad([p[0] - sx, p[1], p[2] - sz], [q[0] - tx, q[1], q[2] - tz], [q[0] + tx, q[1], q[2] + tz], [p[0] + sx, p[1], p[2] + sz], scale(JET, 0.85), 0.6);
  }
}

/** A round basin: outer wall, coping, inner wall, lit water and a bright ring of light just under the coping. */
function basin(m: MeshBuilder, cx: number, cz: number, r: number, y0: number, top: number, waterY: number, segs: number, water: Rgb): void {
  const rim = circlePath(cx, cz, r, segs);
  m.withMat(MAT.concrete, () => {
    ribbon(m, rim, true, 0, y0, 0, top, POOL_WALL, 'left');
    ribbon(m, rim, true, 0, top, 0.7, top, COPING);
    ribbon(m, rim, true, 0.7, top, 0.7, waterY, scale(STONE, 0.9), 'right');
  });
  m.withMat(MAT.water, () => {
    m.disc(cx, waterY, cz, r - 0.65, segs, water, 0.16);
    ribbon(m, circlePath(cx, cz, r - 1.4, segs), true, -0.07, waterY + 0.012, 0.07, waterY + 0.012, hex('#bfe9ff'), 'up', 0.55);
  });
}

export type CenterPlazaOptions = Readonly<{
  /** Radius of the outer basin (default 12.5) and of the paved plaza (default 18.8). */
  basin?: number; paved?: number;
  /** Benches round the fountain (default 12). */
  benches?: number;
}>;

/**
 * The heart of the campus: a paved plaza with concentric paving, a big three tier fountain (outer basin, middle basin, upper
 * basin and a lit globe on a column), a ring of arching jets that fall from tier to tier, lit water and rim lights, benches facing
 * the water, garden lamps, clipped hedge arcs and topiary. Every size scales with the basin radius. Decoration only: no text, no data.
 */
export function centerPlaza(b: MeshBuilder, cx: number, cz: number, o: CenterPlazaOptions = {}): AssetResult {
  const R = o.basin ?? 12.5, P = o.paved ?? 18.8, k = R / 11.5;
  const glow: GlowPoint[] = [];
  return scoped(b, glow, (m) => {
    // Paving: concentric bands in alternating tones and radial joints, so the plaza reads as laid stone.
    m.withMat(MAT.tile, () => {
      const bands: Array<[number, Rgb]> = [[P, mix(STONE, hex('#ffffff'), 0.22)], [P * 0.9, scale(STONE, 0.86)], [P * 0.8, mix(STONE, hex('#ffffff'), 0.3)], [P * 0.7, scale(STONE, 0.8)], [R + 1.5, mix(STONE, hex('#ffffff'), 0.18)]];
      bands.forEach(([r, c], i) => m.disc(cx, 0.02 + i * 0.004, cz, r, 48, c));
      for (let i = 0; i < 32; i++) {
        const a = ((i + 0.5) / 32) * Math.PI * 2, c = Math.cos(a), s = Math.sin(a), w = 0.07;
        flatPoly(m, [[cx + c * (R + 1.6) - s * w, cz + s * (R + 1.6) + c * w], [cx + c * P * 0.98 - s * w, cz + s * P * 0.98 + c * w], [cx + c * P * 0.98 + s * w, cz + s * P * 0.98 - c * w], [cx + c * (R + 1.6) + s * w, cz + s * (R + 1.6) - c * w]], 0.04, scale(STONE, 0.7));
      }
    });
    // Three basins stacked: the outer one on the paving, the middle one rising from its water, the upper one on top.
    const R1 = R, R2 = R * 0.63, R3 = R * 0.31;
    const T1 = 0.9, T2 = 2.35 * k ** 0.5, T3 = 3.75 * k ** 0.5;
    basin(m, cx, cz, R1, 0.02, T1, 0.74, 44, DEEP_WATER);
    basin(m, cx, cz, R2, 0.5, T2, T2 - 0.3, 34, LIT_WATER);
    basin(m, cx, cz, R3, T2 - 0.1, T3, T3 - 0.28, 24, LIT_WATER);
    // Column, gold collar and the globe on top.
    const colTop = T3 + 1.5;
    m.withMat(MAT.concrete, () => {
      m.prism(cx, T3 - 0.2, cz, 1.25, 1.0, 1.7, 14, scale(STONE, 0.8));
      m.prism(cx, colTop - 0.1, cz, 1.6, 1.5, 0.28, 18, COPING);
    });
    m.prism(cx, colTop + 0.15, cz, 1.55, 1.55, 0.14, 18, GOLD, { emissive: 0.9 });
    const gr = 2.45 * Math.sqrt(k), gy = colTop + 0.3 + gr;
    globe(m, cx, gy, cz, gr);
    // Jets. Twenty arch from the middle basin out into the outer one, twelve from the outer rim in, eight from the upper basin
    // down to the middle one, a spout on every second step of the middle rim and six round the column.
    for (let i = 0; i < 20; i++) {
      const a = ((i + 0.25) / 20) * Math.PI * 2;
      jetArc(m, cx, cz, a, [[R2 - 0.5, T2 - 0.1], [R2 + 0.9, T2 + 1.0], [R2 + 2.6, T2 + 0.75], [R2 + 4.0, T1 + 0.9], [R2 + 4.9, T1 - 0.1]], 0.1, 0.07);
    }
    for (let i = 0; i < 12; i++) {
      const a = ((i + 0.6) / 12) * Math.PI * 2;
      jetArc(m, cx, cz, a, [[R1 - 0.9, T1 - 0.1], [R1 - 1.7, T1 + 1.3], [R1 - 3.0, T1 + 1.7], [R1 - 4.3, T1 + 0.7], [R1 - 4.9, T1 - 0.1]], 0.1, 0.08);
    }
    for (let i = 0; i < 8; i++) {
      const a = ((i + 0.5) / 8) * Math.PI * 2;
      jetArc(m, cx, cz, a, [[R3 - 0.4, T3 - 0.1], [R3 + 0.5, T3 + 0.9], [R3 + 1.4, T3 + 0.6], [R3 + 2.1, T2 + 0.5], [R3 + 2.5, T2 - 0.4]], 0.09, 0.07);
    }
    for (let i = 0; i < 10; i++) {
      const a = ((i + 0.5) / 10) * Math.PI * 2, rr = R2 - 0.9;
      m.prism(cx + Math.cos(a) * rr, T2 - 0.3, cz + Math.sin(a) * rr, 0.13, 0.02, 1.7 + (i % 2) * 0.8, 5, JET, { emissive: 0.85, capTop: false });
    }
    for (let i = 0; i < 6; i++) {
      const a = (i / 6) * Math.PI * 2 + 0.3, rr = 1.15;
      m.prism(cx + Math.cos(a) * rr, T3 - 0.25, cz + Math.sin(a) * rr, 0.1, 0.02, 1.4 + (i % 2) * 0.6, 5, JET, { emissive: 0.85, capTop: false });
    }
    // Lit studs on the coping of every basin.
    for (const [rr, y, n] of [[R1 - 0.35, T1 + 0.02, 24], [R2 - 0.35, T2 + 0.02, 16], [R3 - 0.35, T3 + 0.02, 10]] as const) {
      for (let i = 0; i < n; i++) {
        const a = (i / n) * Math.PI * 2;
        m.prism(cx + Math.cos(a) * rr, y, cz + Math.sin(a) * rr, 0.09, 0.09, 0.1, 4, TEAL, { emissive: 0.95 });
      }
    }
    // Round the water: benches facing it, garden lamps between them, topiary and low hedge arcs behind.
    const nb = o.benches ?? 12;
    const rb = R + 3.1 * k, rl = R + 4.9 * k;
    for (let i = 0; i < nb; i++) {
      const a = ((i + 0.5) / nb) * Math.PI * 2 + Math.PI / nb;
      bench(m, cx + Math.cos(a) * rb, cz + Math.sin(a) * rb, { rot: Math.atan2(-Math.cos(a), -Math.sin(a)), style: i % 3 === 0 ? 'stone' : 'slat' });
    }
    for (let i = 0; i < nb / 2; i++) {
      const a = ((i + 0.5) / (nb / 2)) * Math.PI * 2 + Math.PI / nb;
      const x = cx + Math.cos(a) * rl, z = cz + Math.sin(a) * rl;
      glow.push(...lamp(m, x, z, { kind: 'garden' }).glow);
    }
    for (let i = 0; i < 8; i++) {
      const a = (i / 8) * Math.PI * 2;
      topiary(m, cx + Math.cos(a) * (rl + 0.2), cz + Math.sin(a) * (rl + 0.2), 0.8);
    }
    const hedgeR = Math.min(P - 0.9, rl + 1.0);
    for (let i = 0; i < nb; i++) {
      const a0 = (i / nb) * Math.PI * 2 + 0.12, a1 = ((i + 0.5) / nb) * Math.PI * 2 - 0.12;
      const arc: P2[] = [];
      for (let j = 0; j <= 4; j++) { const a = a0 + (a1 - a0) * (j / 4); arc.push([cx + Math.cos(a) * hedgeR, cz + Math.sin(a) * hedgeR]); }
      hedgeRun(m, arc, { height: 0.55, width: 0.55, seed: `plaza-hedge-${i}` });
    }
    // Night: the water and the globe glow, the rim of the plaza is warm.
    b.halo(cx, gy, cz, R * 1.45, hex('#7fc4ff'), 1.5);
    b.halo(cx, 0.9, cz, R * 1.35, hex('#6fd0e8'), 0.95);
    b.halo(cx, T2, cz, R * 0.9, hex('#8fd8ff'), 0.7);
    for (let i = 0; i < 8; i++) {
      const a = (i / 8) * Math.PI * 2 + 0.2;
      b.halo(cx + Math.cos(a) * rl, 1.4, cz + Math.sin(a) * rl, 5.5, WARM, 0.7);
    }
  });
}

export type RoundaboutOptions = Readonly<{
  radius?: number; width?: number;
  /** Angles (radians, from +x towards +z, the way the campus is drawn) where an avenue joins: the kerb, pavement and edge lines open there. */
  mouths?: readonly number[];
  /** Angles where a pedestrian crossing is painted (a mouth is never crossed). */
  crossings?: readonly number[];
}>;

const TAU = Math.PI * 2;
const wrapAngle = (a: number): number => ((a % TAU) + TAU) % TAU;

/** A roundabout: a closed road round (cx, cz) with kerbs, pavements, a dashed centre line, openings for the avenues and zebra crossings. */
export function roundabout(b: MeshBuilder, cx: number, cz: number, o: RoundaboutOptions = {}): AssetResult {
  const r = o.radius ?? 25.8, w = o.width ?? 8;
  const segs = Math.max(24, Math.min(72, Math.round(r * 2.4)));
  const pts = circlePath(cx, cz, r, segs);
  const total = r * TAU;
  const open = (w / 2 + 2.2) / 1; // half the opening along the centre line, metres
  const gaps: Array<readonly [number, number]> = [];
  const mouthAt = (o.mouths ?? []).map(wrapAngle);
  for (const a of mouthAt) {
    const s = a * r;
    if (s - open < 0) { gaps.push([0, s + open], [total + (s - open), total]); } else if (s + open > total) { gaps.push([s - open, total], [0, s + open - total]); } else gaps.push([s - open, s + open]);
  }
  const arcs = (o.crossings ?? []).map(wrapAngle).filter((a) => mouthAt.every((m) => Math.abs(Math.atan2(Math.sin(a - m), Math.cos(a - m))) * r > open + 2.2));
  return road(b, pts, { width: w, closed: true, y: ROAD_Y, gaps, crossings: arcs.map((a) => a * r), centre: 'dashed' });
}

/** A straight avenue between two points (x, z) with pavements and a dashed centre line, and street lamps on both sides. */
export function avenue(b: MeshBuilder, from: readonly [number, number], to: readonly [number, number], o: { width?: number; lampSpacing?: number } = {}): AssetResult {
  const w = o.width ?? 8;
  const glow: GlowPoint[] = [];
  return scoped(b, glow, (m) => {
    const pts: P2[] = [from, to];
    road(m, pts, { width: w, y: ROAD_Y, centre: 'dashed' });
    glow.push(...lampLine(m, pts, { spacing: o.lampSpacing ?? 44, offset: w / 2 + 0.35 + 2.4, kind: 'street' }).glow);
    glow.push(...lampLine(m, pts, { spacing: o.lampSpacing ?? 44, offset: -(w / 2 + 0.35 + 2.4), kind: 'street' }).glow);
  });
}

export const BUILDING_GARDEN_TRIANGLES_CAP = 9000;

type GardenBox = Readonly<{ x: number; z: number; w: number; d: number }>;

/**
 * The garden of one building: paving bands in the apron, hedged lawn beds either side of the entrance
 * walk with a palm and two trees each, shrubs and topiary along the sides. `door` is the walkway end in front.
 */
export function buildingGarden(b: MeshBuilder, foot: GardenBox, door: { x: number; z: number }, o: { seed?: string; lawn?: Rgb } = {}): AssetResult {
  const rnd = seeded(o.seed ?? 'garden');
  const lawn = o.lawn ?? hex('#3d6b3f');
  return scoped(b, [], (m) => {
    const front = foot.z + foot.d;
    const apron = 5.0;
    // Paving bands across the apron in front of the building, a lighter walk to the door.
    m.withMat(MAT.tile, () => {
      for (let i = 0; i < 4; i++) {
        const z = front + 1.2 + i * 2.6;
        m.plane(foot.x - 2, 0.03 + i * 0.002, z, foot.w + 4, 1.2, i % 2 ? scale(STONE, 0.86) : mix(STONE, hex('#ffffff'), 0.2));
      }
      m.plane(door.x - 2.4, 0.045, front, 4.8, door.z - front + 0.2, mix(STONE, hex('#ffffff'), 0.32));
    });
    // Lawn beds either side of the walk, bordered by clipped hedges, each with a palm, a tree and topiary.
    const bedD = Math.max(5, Math.min(11, door.z - front - 1));
    for (const side of [-1, 1] as const) {
      const bx0 = side < 0 ? foot.x - 1.5 : door.x + 3.4;
      const bx1 = side < 0 ? door.x - 3.4 : foot.x + foot.w + 1.5;
      const bw = bx1 - bx0;
      if (bw < 3) continue;
      const bz = door.z - bedD - 0.5;
      m.withMat(MAT.grass, () => m.plane(bx0, 0.034, bz, bw, bedD, lawn));
      hedgeRun(m, [[bx0, bz], [bx1, bz], [bx1, bz + bedD], [bx0, bz + bedD]], { closed: true, height: 0.75, width: 0.6, seed: `${o.seed}-h${side}` });
      tree(m, bx0 + bw * 0.5, bz + bedD * 0.5, { species: 'datePalm', height: 9 + rnd() * 2, seed: `${o.seed}-p${side}` });
      if (bw > 8) {
        tree(m, bx0 + bw * 0.2, bz + bedD * 0.7, { species: rnd() < 0.5 ? 'oak' : 'flowering', height: 5.4, seed: `${o.seed}-a${side}` });
        topiary(m, bx0 + bw * 0.85, bz + bedD * 0.3, 0.7);
      }
      shrub(m, bx0 + 1, bz + bedD - 1, 0.8, o.seed);
    }
    // Side and back planting: a tree row along each flank and a few shrubs.
    for (const sx of [foot.x - apron, foot.x + foot.w + apron]) {
      for (let z = foot.z + 3; z < front - 2; z += 7.5) {
        tree(m, sx + (rnd() - 0.5) * 1.2, z, { species: rnd() < 0.5 ? 'datePalm' : 'oak', seed: `${o.seed}-s${sx.toFixed(0)}-${z.toFixed(0)}` });
        if (rnd() < 0.7) shrub(m, sx + (sx < foot.x ? 1.6 : -1.6), z + 2, 0.7, o.seed);
      }
    }
    for (let x = foot.x + 3; x < foot.x + foot.w - 2; x += 8) tree(m, x, foot.z - 4.5, { species: rnd() < 0.5 ? 'cypress' : 'olive', seed: `${o.seed}-b${x.toFixed(0)}` });
    b.halo(door.x, 2.4, front + 2, 8, WARM, 0.6);
  });
}
