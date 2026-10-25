/**
 * A far skyline backdrop: towers of several shapes (slab, setback, tapered, twin, round, spire)
 * placed on an arc outside the campus, each turned to face the campus centre, with lit window
 * bands, haze towards the horizon colour and aviation lights. A backdrop, not data: nothing in it
 * stands for a company, a count or a status.
 */
import { MeshBuilder, hex, mix, scale, seeded, type Rgb } from '../../lib/hqGeometry.ts';
import { WARM, WINDOW_BASE } from '../../lib/hqProps.ts';
import { Frame, pick, range, scoped, type AssetResult } from './common.ts';

export type TowerShape = 'slab' | 'setback' | 'tapered' | 'twin' | 'round' | 'spire';
export const TOWER_TRIANGLES_CAP = 280;
const HAZE = hex('#b4c4d4');

/** One tower facing +z (rotate with `rot` heading), height `h`, footprint w by d. */
export function skylineTower(
  b: MeshBuilder, x: number, z: number, o: { shape?: TowerShape; w?: number; d?: number; h?: number; rot?: number; haze?: number; seed?: string } = {},
): AssetResult {
  const rnd = seeded(o.seed ?? `tower-${x.toFixed(0)}-${z.toFixed(0)}`);
  const shape = o.shape ?? pick(rnd, ['slab', 'setback', 'tapered', 'twin', 'round', 'spire'] as const);
  const w = o.w ?? range(rnd, 14, 24), d = o.d ?? range(rnd, 12, 20), h = o.h ?? range(rnd, 50, 130);
  const haze = o.haze ?? 0.3;
  const wall: Rgb = mix(mix(hex('#5c6b7c'), hex('#8794a3'), rnd()), HAZE, haze);
  const lit = (c: Rgb): Rgb => mix(c, HAZE, haze * 0.6);
  return scoped(b, [], (m) => {
    const f = new Frame(m, x, 0, z, o.rot ?? 0);
    const bands = (cx: number, cz: number, ww: number, dd: number, y0: number, y1: number) => {
      const rows = Math.max(2, Math.floor((y1 - y0) / 3.6));
      for (let i = 1; i < rows; i++) {
        if (rnd() < 0.18) continue;
        const y = y0 + i * 3.6;
        const tone = lit(mix(WARM, hex('#cfe6ff'), rnd() * 0.6));
        const em = WINDOW_BASE + (rnd() < 0.3 ? 0.3 : 0.6);
        f.quadToward([cx - ww / 2 + 0.6, y, cz + dd / 2 + 0.04], [cx + ww / 2 - 0.6, y, cz + dd / 2 + 0.04], [cx + ww / 2 - 0.6, y + 1.5, cz + dd / 2 + 0.04], [cx - ww / 2 + 0.6, y + 1.5, cz + dd / 2 + 0.04], tone, [0, 0, 1], em);
        f.quadToward([cx + ww / 2 + 0.04, y, cz - dd / 2 + 0.6], [cx + ww / 2 + 0.04, y, cz + dd / 2 - 0.6], [cx + ww / 2 + 0.04, y + 1.5, cz + dd / 2 - 0.6], [cx + ww / 2 + 0.04, y + 1.5, cz - dd / 2 + 0.6], tone, [1, 0, 0], em);
      }
    };
    f.shadow(-w / 2, -d / 2, w, d, h * 0.5, 0.02);
    const aviation = (ax: number, ay: number, az: number) => f.box(ax - 0.3, ay, az - 0.3, 0.6, 0.6, 0.6, hex('#ff4a3a'), { emissive: 1, flat: true });
    if (shape === 'slab') {
      f.box(-w / 2, -0.05, -d / 2, w, h, d, wall, { grad: 0.3 });
      bands(0, 0, w, d, 0, h);
      f.box(-w * 0.3, h, -d * 0.3, w * 0.6, 2.5, d * 0.6, scale(wall, 0.85));
      aviation(0, h + 2.5, 0);
    } else if (shape === 'setback') {
      const h1 = h * 0.6, h2 = h * 0.28, h3 = h * 0.12;
      f.box(-w / 2, -0.05, -d / 2, w, h1, d, wall, { grad: 0.3 });
      bands(0, 0, w, d, 0, h1);
      f.box(-w * 0.38, h1, -d * 0.38, w * 0.76, h2, d * 0.76, scale(wall, 1.05), { grad: 0.2 });
      bands(0, 0, w * 0.76, d * 0.76, h1, h1 + h2);
      f.box(-w * 0.22, h1 + h2, -d * 0.22, w * 0.44, h3, d * 0.44, scale(wall, 1.1));
      f.cyl(0, h1 + h2 + h3, 0, 'y', 0.4, 0.05, h * 0.18, 5, hex('#8a949f'));
      aviation(0, h1 + h2 + h3 + h * 0.18, 0);
    } else if (shape === 'tapered') {
      const top = 0.55;
      const pts: Array<[number, number, number, number]> = [[-w / 2, -d / 2, -w * top / 2, -d * top / 2], [w / 2, -d / 2, w * top / 2, -d * top / 2], [w / 2, d / 2, w * top / 2, d * top / 2], [-w / 2, d / 2, -w * top / 2, d * top / 2]];
      for (let i = 0; i < 4; i++) {
        const p = pts[i], q = pts[(i + 1) % 4];
        f.face([[p[0], 0, p[1]], [q[0], 0, q[1]], [q[2], h, q[3]], [p[2], h, p[3]]], [0, h / 2, 0], [wall, wall, scale(wall, 1.12), scale(wall, 1.12)]);
      }
      f.face(pts.map((p) => [p[2], h, p[3]] as [number, number, number]), [0, h / 2, 0], [wall, wall, wall, wall]);
      bands(0, 0, w * 0.8, d * 0.8, 0, h * 0.8);
      aviation(0, h, 0);
    } else if (shape === 'twin') {
      const tw = w * 0.42;
      for (const sx of [-1, 1]) {
        f.box(sx * w * 0.28 - tw / 2, -0.05, -d / 2, tw, h, d, wall, { grad: 0.3 });
        bands(sx * w * 0.28, 0, tw, d, 0, h);
        aviation(sx * w * 0.28, h, 0);
      }
      f.box(-w * 0.1, h * 0.7, -d * 0.3, w * 0.2, 3, d * 0.6, scale(wall, 0.9));
    } else if (shape === 'round') {
      f.cyl(0, -0.05, 0, 'y', w * 0.45, w * 0.4, h, 8, wall, { capEnd: true });
      const rows = Math.floor(h / 6);
      for (let i = 1; i < rows; i++) {
        if (rnd() < 0.18) continue;
        const y = i * 6, r = w * 0.45 - (w * 0.05 * y) / h + 0.04;
        f.cyl(0, y, 0, 'y', r, r, 1.6, 8, lit(mix(WARM, hex('#cfe6ff'), rnd() * 0.6)), { emissive: WINDOW_BASE + 0.5 });
      }
      f.cyl(0, h, 0, 'y', w * 0.4, w * 0.3, 3, 8, scale(wall, 1.1), { capEnd: true });
      aviation(0, h + 3, 0);
    } else {
      f.box(-w / 2, -0.05, -d / 2, w, h * 0.78, d, wall, { grad: 0.3 });
      bands(0, 0, w, d, 0, h * 0.78);
      f.cyl(0, h * 0.78, 0, 'y', w * 0.3, 0.2, h * 0.22, 6, scale(wall, 1.1));
      aviation(0, h, 0);
    }
  });
}

export type SkylineOptions = {
  cx: number; cz: number; radius: number;
  /** Arc in radians (0 = +x east, increasing towards +z south). Default: the northern half. */
  fromAngle?: number; toAngle?: number;
  count?: number; minHeight?: number; maxHeight?: number; seed?: string;
};
export const SKYLINE_TRIANGLES_PER_TOWER_CAP = TOWER_TRIANGLES_CAP;

/** Towers along an arc, taller towards the middle of the arc, hazier the farther away. */
export function skylineBackdrop(b: MeshBuilder, o: SkylineOptions): AssetResult {
  const rnd = seeded(o.seed ?? 'skyline');
  const n = o.count ?? 24;
  const a0 = o.fromAngle ?? Math.PI, a1 = o.toAngle ?? Math.PI * 2;
  const lo = o.minHeight ?? 35, hi = o.maxHeight ?? 150;
  return scoped(b, [], (m) => {
    for (let i = 0; i < n; i++) {
      const t = (i + 0.5) / n;
      const a = a0 + (a1 - a0) * t + (rnd() - 0.5) * ((a1 - a0) / n) * 0.6;
      const rr = o.radius * range(rnd, 0.94, 1.12);
      const px = o.cx + Math.cos(a) * rr, pz = o.cz + Math.sin(a) * rr;
      const peak = 0.55 + 0.45 * Math.sin(Math.PI * t);
      const h = range(rnd, lo, lo + (hi - lo) * peak);
      const rot = Math.atan2(o.cx - px, o.cz - pz);
      skylineTower(m, px, pz, { h, rot, haze: 0.25 + 0.4 * ((rr / o.radius - 0.9) / 0.25), seed: `${o.seed ?? 'skyline'}-${i}` });
    }
  });
}
